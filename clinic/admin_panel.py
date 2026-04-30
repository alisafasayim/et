"""
Hafif admin / sağlık paneli.

Mevcut Flask webhook sunucusuna ek endpoint'ler ekler:
  GET  /admin/health             — sistem ayakta mı, alt servisler
  GET  /admin/state/summary      — state_store namespace sayıları
  GET  /admin/state/<ns>?key=... — bir kaydı sorgula
  POST /admin/state/forget       — manuel reset (idempotency reset)
  GET  /admin/upcoming           — yaklaşan randevular özeti
  POST /admin/trigger/reminder   — manuel hatırlatma cron tetikle
  POST /admin/trigger/esmm       — manuel e-SMM (formdan)

Auth: ADMIN_TOKEN env değişkeni; her istekte
    Authorization: Bearer <token>
veya ?token=... query string. Set edilmemişse panel devre dışı (404).
"""

import logging
import os
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, abort, jsonify, request

logger = logging.getLogger("admin_panel")

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _require_admin(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not ADMIN_TOKEN:
            # Panel devre dışı; 404 ile var olmadığı izlenimi ver.
            abort(404)
        provided = ""
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            provided = auth[7:]
        elif request.args.get("token"):
            provided = request.args["token"]
        if provided != ADMIN_TOKEN:
            abort(401)
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@bp.route("/health", methods=["GET"])
@_require_admin
def health():
    """Sistem ayakta + temel servislerin imza durumu."""
    from state_store import get_default_store

    out = {
        "status": "ok",
        "now": datetime.now(tz=timezone.utc).isoformat(),
        "services": {},
    }

    # state_store
    try:
        store = get_default_store()
        out["services"]["state_store"] = {"status": "ok", "db": str(store.db_path)}
    except Exception as exc:
        out["services"]["state_store"] = {"status": "error", "error": str(exc)}

    # WhatsApp instance bağlantısı
    try:
        from module3_whatsapp_communicator import get_instance_status
        status = get_instance_status()
        state = status.get("instance", {}).get("state", "unknown")
        out["services"]["whatsapp"] = {"status": "ok", "state": state}
    except Exception as exc:
        out["services"]["whatsapp"] = {"status": "error", "error": str(exc)}

    return jsonify(out)


@bp.route("/state/summary", methods=["GET"])
@_require_admin
def state_summary():
    """state_store namespace başına kayıt sayıları."""
    from state_store import get_default_store
    store = get_default_store()
    with store._cursor() as cur:
        cur.execute(
            "SELECT namespace, COUNT(*) FROM processed GROUP BY namespace ORDER BY namespace"
        )
        rows = cur.fetchall()
    return jsonify({ns: count for ns, count in rows})


@bp.route("/state/<namespace>", methods=["GET"])
@_require_admin
def state_lookup(namespace: str):
    """Bir namespace+key kombinasyonu işaretli mi?"""
    key = request.args.get("key", "")
    if not key:
        abort(400, "key parametresi zorunlu")
    from state_store import get_default_store
    store = get_default_store()
    return jsonify({"namespace": namespace, "key": key, "seen": store.is_seen(namespace, key)})


@bp.route("/state/forget", methods=["POST"])
@_require_admin
def state_forget():
    """Manuel state reset — idempotency anahtarını siler."""
    payload = request.get_json(silent=True) or {}
    namespace = payload.get("namespace") or request.args.get("namespace", "")
    key = payload.get("key") or request.args.get("key", "")
    if not namespace or not key:
        abort(400, "namespace ve key zorunlu")
    from state_store import get_default_store
    store = get_default_store()
    store.forget(namespace, key)
    logger.warning("Admin manuel forget: %s/%s", namespace, key)
    return jsonify({"status": "ok", "namespace": namespace, "key": key})


@bp.route("/upcoming", methods=["GET"])
@_require_admin
def upcoming():
    """Yaklaşan randevu özeti (sonraki 7 gün)."""
    try:
        from datetime import timedelta as _td
        from module3_whatsapp_communicator import (
            fetch_upcoming_appointments,
            get_calendar_service,
        )
        service = get_calendar_service()
        now = datetime.now(tz=timezone.utc)
        appts = fetch_upcoming_appointments(service, now, now + _td(days=7))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        [
            {
                "event_id": a["event_id"],
                "summary": a.get("summary", ""),
                "start": a["start"],
                "has_phone": bool(a.get("phone")),
            }
            for a in appts
        ]
    )


@bp.route("/trigger/reminder", methods=["POST"])
@_require_admin
def trigger_reminder():
    """Manuel hatırlatma cron tetikleme (24h veya 1h)."""
    horizon = (request.args.get("horizon") or "24h").strip()
    if horizon not in ("24h", "1h"):
        abort(400, "horizon: 24h | 1h")
    from module3_whatsapp_communicator import poll_upcoming_reminders
    results = poll_upcoming_reminders(horizon)
    return jsonify({"horizon": horizon, "results": results})


@bp.route("/trigger/esmm", methods=["POST"])
@_require_admin
def trigger_esmm_endpoint():
    """Manuel e-SMM tetikleme. JSON body: trigger_esmm signature ile aynı."""
    payload = request.get_json(silent=True) or {}
    required = ("patient_name", "guardian_phone", "tax_id", "amount")
    if not all(payload.get(k) for k in required):
        abort(400, f"Zorunlu alanlar: {required}")
    try:
        from main import trigger_esmm
        result = trigger_esmm(
            patient_name=payload["patient_name"],
            guardian_phone=payload["guardian_phone"],
            tax_id=payload["tax_id"],
            amount=payload["amount"],
            description=payload.get("description", "Çocuk ve Ergen Psikiyatrisi Muayenesi"),
            appointment_date=payload.get("appointment_date", ""),
            collection_key=payload.get("collection_key", ""),
        )
    except Exception as exc:
        logger.error("Admin trigger_esmm hatası: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500
    return jsonify(result)


def register(flask_app) -> bool:
    """
    Mevcut Flask uygulamasına admin blueprint'ini ekler.
    ADMIN_TOKEN set değilse blueprint kayıt edilir ama her endpoint
    404 döner — daha güvenli (panel keşfedilemez).
    Döner: True kayıt yapıldı / False atlandı.
    """
    flask_app.register_blueprint(bp)
    if ADMIN_TOKEN:
        logger.info("Admin paneli aktif (/admin/*).")
        return True
    logger.info("Admin paneli devre dışı (ADMIN_TOKEN ayarlı değil).")
    return False
