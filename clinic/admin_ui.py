"""
Admin Web UI — Flask + Jinja2.

Mevcut /admin/* JSON API'sinin yanı sıra doktorun tarayıcıdan
kullanabileceği HTML arayüz. Cookie tabanlı oturum (Flask
session, signed). Tek doktor / küçük ekip senaryosu için
amaçlanmıştır; multi-user RBAC YOK.

Endpoint'ler (/ui prefix):
  GET  /ui/login                 — token giriş formu
  POST /ui/login                 — cookie set + redirect
  GET  /ui/logout                — cookie clear
  GET  /ui/                      — dashboard
  GET  /ui/patients              — hasta listesi + arama (?q=)
  POST /ui/patients              — yeni hasta kaydı
  GET  /ui/patients/<uuid>       — hasta detayı + audit timeline
  POST /ui/patients/<uuid>/consent
  POST /ui/patients/<uuid>/delete (KVKK m.7)
  GET  /ui/audit                 — audit log tablosu (?action=...)
  POST /ui/trigger/reminder      — manuel hatırlatma
  POST /ui/trigger/esmm          — manuel e-SMM (form)

Güvenlik:
- ADMIN_TOKEN ile login; doğru ise Flask session cookie'sine
  imzalı flag set edilir.
- Session cookie HttpOnly + SameSite=Lax. HTTPS ortamında
  Secure=True (FLASK_ENV=production veya WEBHOOK_PUBLIC_URL https
  başlıyorsa otomatik).
- CSRF: form'lar Flask session'ından CSRF token alır.
"""

import logging
import os
import secrets
import time
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

logger = logging.getLogger("admin_ui")

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
WEBHOOK_PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "")
LOGIN_MAX_FAILURES = int(os.getenv("ADMIN_LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCKOUT_SECONDS = int(os.getenv("ADMIN_LOGIN_LOCKOUT_SECONDS", "900"))

TEMPLATE_DIR = Path(__file__).parent / "templates"

bp = Blueprint(
    "admin_ui",
    __name__,
    url_prefix="/ui",
    template_folder=str(TEMPLATE_DIR),
)

_login_failures: dict[str, list[float]] = {}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _is_logged_in() -> bool:
    return bool(session.get("admin_authenticated"))


def _require_login(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not ADMIN_TOKEN:
            abort(404)  # UI tamamen kapalı
        if not _is_logged_in():
            return redirect(url_for("admin_ui.login", next=request.path))
        # Audit
        try:
            from audit_log import audit
            audit(
                "admin_ui.access",
                actor="admin_ui",
                details={"endpoint": request.path, "method": request.method},
            )
        except Exception:
            pass
        return view(*args, **kwargs)
    return wrapper


def _csrf_token() -> str:
    """Session başına CSRF token üret."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _validate_csrf() -> None:
    """POST endpoint'lerinde CSRF doğrulaması."""
    submitted = request.form.get("csrf_token", "")
    if not submitted or submitted != session.get("csrf_token", ""):
        abort(403, "CSRF doğrulama başarısız")


def _safe_next_url(target: str | None) -> str:
    """Allow redirects only inside the admin UI."""
    default = url_for("admin_ui.dashboard")
    if not target:
        return default
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return default
    if not parsed.path.startswith("/ui"):
        return default
    return target


def _login_key() -> str:
    return request.remote_addr or "unknown"


def _prune_login_failures(key: str, now: float | None = None) -> list[float]:
    now = time.time() if now is None else now
    cutoff = now - LOGIN_LOCKOUT_SECONDS
    failures = [ts for ts in _login_failures.get(key, []) if ts >= cutoff]
    if failures:
        _login_failures[key] = failures
    else:
        _login_failures.pop(key, None)
    return failures


def _login_locked(key: str) -> bool:
    return len(_prune_login_failures(key)) >= LOGIN_MAX_FAILURES


def _record_login_failure(key: str) -> None:
    failures = _prune_login_failures(key)
    failures.append(time.time())
    _login_failures[key] = failures


def _clear_login_failures(key: str) -> None:
    _login_failures.pop(key, None)


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    if not ADMIN_TOKEN:
        abort(404)

    if request.method == "POST":
        key = _login_key()
        if _login_locked(key):
            abort(429, "Too many login attempts")

        token = request.form.get("token", "")
        if token == ADMIN_TOKEN:
            _clear_login_failures(key)
            session.clear()
            session["admin_authenticated"] = True
            session.permanent = True
            try:
                from audit_log import audit
                audit("admin_ui.login_ok", actor="admin_ui")
            except Exception:
                pass
            next_url = _safe_next_url(request.args.get("next"))
            return redirect(next_url)
        try:
            from audit_log import audit
            audit("admin_ui.login_fail", actor="admin_ui",
                  details={"ip": request.remote_addr})
        except Exception:
            pass
        _record_login_failure(key)
        flash("Geçersiz token", "error")
    return render_template("admin/login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin_ui.login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@bp.route("/")
@_require_login
def dashboard():
    # Health bilgisi
    health = {"whatsapp": "?", "state_store": "?"}
    try:
        from module3_whatsapp_communicator import get_instance_status
        s = get_instance_status()
        health["whatsapp"] = s.get("instance", {}).get("state", "unknown")
    except Exception as exc:
        health["whatsapp"] = f"hata: {exc}"

    try:
        from state_store import get_default_store
        store = get_default_store()
        with store._cursor() as cur:
            cur.execute(
                "SELECT namespace, COUNT(*) FROM processed GROUP BY namespace "
                "ORDER BY namespace"
            )
            ns_counts = cur.fetchall()
        health["state_store"] = "ok"
    except Exception as exc:
        ns_counts = []
        health["state_store"] = f"hata: {exc}"

    # Son audit event'leri
    try:
        from audit_log import get_default_audit_log
        recent_audit = get_default_audit_log().query(limit=10)
    except Exception:
        recent_audit = []

    # İş hacmi istatistikleri (yerel SQLite, hızlı sorgu)
    try:
        from dashboard_stats import compute_summary
        stats = compute_summary()
    except Exception as exc:
        logger.warning("Dashboard stats hesaplanamadı: %s", exc)
        stats = None

    return render_template(
        "admin/dashboard.html",
        health=health,
        namespace_counts=ns_counts,
        recent_audit=recent_audit,
        stats=stats,
        csrf_token=_csrf_token(),
    )


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------

@bp.route("/patients", methods=["GET", "POST"])
@_require_login
def patients():
    from patient_registry import get_default_registry

    registry = get_default_registry()

    if request.method == "POST":
        _validate_csrf()
        full_name = (request.form.get("full_name") or "").strip()
        if not full_name:
            flash("Hasta adı zorunlu", "error")
            return redirect(url_for("admin_ui.patients"))
        uid = registry.create_patient(
            full_name=full_name,
            tax_id=(request.form.get("tax_id") or "").strip(),
            phone=(request.form.get("phone") or "").strip(),
            birth_date=(request.form.get("birth_date") or "").strip(),
        )
        if request.form.get("consent_now"):
            registry.record_consent(uid)
        flash("Hasta kaydedildi", "success")
        return redirect(url_for("admin_ui.patient_detail", patient_uuid=uid))

    # GET: liste + arama
    q = (request.args.get("q") or "").strip()
    if q:
        # tax_id veya isim olabilir; her ikisini dene
        results = []
        if q.isdigit() and len(q) in (10, 11):
            r = registry.find_by_tax_id(q)
            if r:
                results = [r]
        else:
            results = registry.find_by_name(q)
    else:
        results = registry.list_all(limit=200)

    return render_template(
        "admin/patient_list.html",
        patients=results,
        query=q,
        csrf_token=_csrf_token(),
    )


@bp.route("/patients/<patient_uuid>", methods=["GET"])
@_require_login
def patient_detail(patient_uuid: str):
    from patient_registry import get_default_registry
    registry = get_default_registry()
    patient = registry.get_patient(patient_uuid)
    if not patient:
        abort(404)

    # Bu hastaya ait audit timeline
    try:
        from audit_log import get_default_audit_log
        events = get_default_audit_log().query(patient_uuid=patient_uuid, limit=100)
    except Exception:
        events = []

    return render_template(
        "admin/patient_detail.html",
        patient=patient,
        events=events,
        csrf_token=_csrf_token(),
    )


@bp.route("/patients/<patient_uuid>/consent", methods=["POST"])
@_require_login
def patient_consent(patient_uuid: str):
    _validate_csrf()
    from patient_registry import get_default_registry
    registry = get_default_registry()
    if registry.get_patient(patient_uuid) is None:
        abort(404)
    action = request.form.get("action", "grant")
    if action == "revoke":
        registry.revoke_consent(patient_uuid)
        flash("Açık rıza geri çekildi", "success")
    else:
        registry.record_consent(patient_uuid)
        flash("Açık rıza kaydedildi", "success")
    return redirect(url_for("admin_ui.patient_detail", patient_uuid=patient_uuid))


@bp.route("/patients/<patient_uuid>/delete", methods=["POST"])
@_require_login
def patient_delete(patient_uuid: str):
    _validate_csrf()
    from patient_registry import get_default_registry
    registry = get_default_registry()
    deleted = registry.delete_patient(patient_uuid)
    if deleted:
        flash("Hasta kaydı silindi (KVKK m.7)", "success")
    else:
        flash("Hasta bulunamadı", "error")
    return redirect(url_for("admin_ui.patients"))


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@bp.route("/audit")
@_require_login
def audit_view():
    from audit_log import get_default_audit_log
    rows = get_default_audit_log().query(
        action=request.args.get("action") or None,
        actor=request.args.get("actor") or None,
        patient_uuid=request.args.get("patient_uuid") or None,
        limit=int(request.args.get("limit", "200")),
    )
    return render_template(
        "admin/audit.html",
        events=rows,
        filter_action=request.args.get("action", ""),
        filter_actor=request.args.get("actor", ""),
        csrf_token=_csrf_token(),
    )


# ---------------------------------------------------------------------------
# Manuel tetikleyiciler
# ---------------------------------------------------------------------------

@bp.route("/trigger/reminder", methods=["POST"])
@_require_login
def trigger_reminder():
    _validate_csrf()
    horizon = request.form.get("horizon", "24h")
    if horizon not in ("24h", "1h"):
        flash("Geçersiz horizon", "error")
        return redirect(url_for("admin_ui.dashboard"))
    try:
        from module3_whatsapp_communicator import poll_upcoming_reminders
        results = poll_upcoming_reminders(horizon)
        sent = sum(1 for r in results if r.get("status") == "sent")
        flash(f"Hatırlatma tetiklendi ({horizon}): {sent} mesaj gönderildi", "success")
    except Exception as exc:
        flash(f"Hata: {exc}", "error")
    return redirect(url_for("admin_ui.dashboard"))


@bp.route("/trigger/esmm", methods=["GET", "POST"])
@_require_login
def trigger_esmm():
    if request.method == "POST":
        _validate_csrf()
        required = ("patient_name", "guardian_phone", "tax_id", "amount")
        if not all(request.form.get(k) for k in required):
            flash("Tüm zorunlu alanları doldurun", "error")
            return redirect(url_for("admin_ui.trigger_esmm"))
        try:
            from main import trigger_esmm
            result = trigger_esmm(
                patient_name=request.form["patient_name"],
                guardian_phone=request.form["guardian_phone"],
                tax_id=request.form["tax_id"],
                amount=request.form["amount"],
                description=request.form.get("description", "Çocuk ve Ergen Psikiyatrisi Muayenesi"),
                appointment_date=request.form.get("appointment_date", ""),
                collection_key=request.form.get("collection_key", ""),
            )
            flash(f"e-SMM süreci: {result.get('status')}", "success")
        except Exception as exc:
            flash(f"Hata: {exc}", "error")
        return redirect(url_for("admin_ui.dashboard"))
    return render_template("admin/esmm_form.html", csrf_token=_csrf_token())


# ---------------------------------------------------------------------------
# Yaklaşan randevular
# ---------------------------------------------------------------------------

@bp.route("/upcoming")
@_require_login
def upcoming():
    try:
        from datetime import datetime, timedelta, timezone
        from module3_whatsapp_communicator import (
            fetch_upcoming_appointments,
            get_calendar_service,
        )
        service = get_calendar_service()
        now = datetime.now(tz=timezone.utc)
        appts = fetch_upcoming_appointments(service, now, now + timedelta(days=7))
    except Exception as exc:
        appts = []
        flash(f"Calendar erişim hatası: {exc}", "error")
    return render_template("admin/upcoming.html", appointments=appts)


# ---------------------------------------------------------------------------
# Kayıt yardımcısı
# ---------------------------------------------------------------------------

def register(flask_app) -> bool:
    """
    Mevcut Flask uygulamasına admin UI blueprint'ini kaydeder.
    ADMIN_TOKEN set değilse blueprint kaydedilir ama tüm endpoint'ler
    404 döner. Flask session secret_key ihtiyaç duyar; önceden set
    edilmemişse SECRET_KEY env veya rastgele üretir (proses ömrü).
    """
    if not flask_app.secret_key:
        flask_app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
    # Cookie güvenliği
    flask_app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    flask_app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    if WEBHOOK_PUBLIC_URL.startswith("https://"):
        flask_app.config.setdefault("SESSION_COOKIE_SECURE", True)

    flask_app.register_blueprint(bp)
    if ADMIN_TOKEN:
        logger.info("Admin UI aktif: /ui/login")
        return True
    logger.info("Admin UI devre dışı (ADMIN_TOKEN yok).")
    return False
