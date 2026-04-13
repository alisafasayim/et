"""
WhatsApp Webhook Sunucusu
=========================
Gelen WhatsApp mesajlarını chatbot'a ileten hafif Flask sunucusu.

Hem Twilio hem Evolution API webhook'larını destekler.

Kullanım:
    python scripts/webhook_server.py

Ngrok ile public URL almak için (geliştirme):
    ngrok http 5000

Production için:
    gunicorn -w 2 scripts.webhook_server:app

Twilio Webhook URL'ini ayarlayın:
    https://your-domain.com/webhook/twilio

Evolution API Webhook URL'ini ayarlayın:
    https://your-domain.com/webhook/evolution
"""

import sys
import json
import hmac
import hashlib
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from flask import Flask, request, Response
from clinic_automation.config.settings import get_config
from clinic_automation.modules.chatbot import ChatbotEngine
from clinic_automation.modules.whatsapp import WhatsAppAutomation
from clinic_automation.utils.security import AuditLogger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

config = get_config()
app = Flask(__name__)

chatbot = ChatbotEngine(config.whatsapp)
whatsapp = WhatsAppAutomation(config.whatsapp)
audit = AuditLogger(config.security.audit_log_path)


# ─────────────────── Twilio Webhook ───────────────────

@app.route("/webhook/twilio", methods=["POST"])
def twilio_webhook():
    """Twilio'dan gelen WhatsApp mesajlarını işler."""
    # Twilio imza doğrulaması
    if not _verify_twilio_signature(request):
        logger.warning("Geçersiz Twilio imzası: %s", request.remote_addr)
        return Response("Unauthorized", status=401)

    from_number = request.form.get("From", "").replace("whatsapp:", "")
    body = request.form.get("Body", "").strip()

    if not from_number or not body:
        return _twilio_response("")

    logger.info("Twilio mesajı: %s -> '%s'", from_number, body[:50])
    audit.log_api_call("twilio_webhook", f"message_from_{from_number}", "received")

    # Chatbot'a ilet
    response_text = chatbot.process_message(from_number, body)

    # Yanıt gönder (TwiML formatı)
    return _twilio_response(response_text)


def _twilio_response(message: str) -> Response:
    """TwiML formatında yanıt üretir."""
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{message}</Message>
</Response>"""
    return Response(twiml, mimetype="text/xml")


def _verify_twilio_signature(request) -> bool:
    """Twilio webhook imzasını doğrular (güvenlik)."""
    auth_token = config.whatsapp.twilio_auth_token
    if not auth_token:
        logger.warning("Twilio auth token ayarlanmamış, imza doğrulaması atlandı.")
        return True  # Development mode

    signature = request.headers.get("X-Twilio-Signature", "")
    url = request.url
    params = request.form.to_dict()

    # Parametreleri sırala ve birleştir
    if params:
        sorted_params = "".join(f"{k}{v}" for k, v in sorted(params.items()))
        url_with_params = url + sorted_params
    else:
        url_with_params = url

    expected = hmac.new(
        auth_token.encode("utf-8"),
        url_with_params.encode("utf-8"),
        hashlib.sha1,
    ).digest()

    import base64
    expected_b64 = base64.b64encode(expected).decode("utf-8")
    return hmac.compare_digest(signature, expected_b64)


# ─────────────────── Evolution API Webhook ───────────────────

@app.route("/webhook/evolution", methods=["POST"])
def evolution_webhook():
    """Evolution API'den gelen mesajları işler."""
    api_key = config.whatsapp.evolution_api_key
    if api_key:
        received_key = request.headers.get("apikey", "")
        if not hmac.compare_digest(received_key, api_key):
            logger.warning("Geçersiz Evolution API anahtarı: %s", request.remote_addr)
            return Response("Unauthorized", status=401)

    try:
        data = request.get_json(force=True)
    except Exception:
        return Response("Bad Request", status=400)

    if not data:
        return Response("OK", status=200)

    # Evolution API mesaj formatı
    event = data.get("event", "")
    if event != "messages.upsert":
        return Response("OK", status=200)

    messages = data.get("data", {}).get("messages", [])
    for msg in messages:
        # Kendi gönderdiğimiz mesajları atla
        if msg.get("key", {}).get("fromMe"):
            continue

        from_number = msg.get("key", {}).get("remoteJid", "").split("@")[0]
        if not from_number:
            continue

        # Metin mesajı
        body = (
            msg.get("message", {}).get("conversation") or
            msg.get("message", {}).get("extendedTextMessage", {}).get("text", "")
        )
        if not body:
            continue

        logger.info("Evolution mesajı: %s -> '%s'", from_number, body[:50])
        audit.log_api_call("evolution_webhook", f"message_from_{from_number}", "received")

        # Chatbot yanıtı
        response_text = chatbot.process_message(f"+{from_number}", body)

        if response_text:
            try:
                whatsapp.provider.send_message(f"+{from_number}", response_text)
            except Exception as e:
                logger.error("Yanıt gönderilemedi (%s): %s", from_number, e)

    return Response("OK", status=200)


# ─────────────────── Sağlık Kontrolü ───────────────────

@app.route("/health", methods=["GET"])
def health_check():
    """Sunucu sağlık kontrolü."""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "chatbot": "enabled" if config.whatsapp.chatbot_enabled else "disabled",
        "provider": config.whatsapp.provider,
    }


@app.route("/", methods=["GET"])
def index():
    return {
        "service": "Klinik Otomasyon WhatsApp Webhook",
        "endpoints": {
            "/webhook/twilio": "Twilio webhook",
            "/webhook/evolution": "Evolution API webhook",
            "/health": "Sağlık kontrolü",
        },
    }


if __name__ == "__main__":
    import os
    port = int(os.getenv("WEBHOOK_PORT", 5000))
    debug = config.debug

    logger.info("WhatsApp Webhook Sunucusu başlatılıyor (port: %d)", port)
    logger.info("Sağlayıcı: %s | Chatbot: %s",
                config.whatsapp.provider,
                "Aktif" if config.whatsapp.chatbot_enabled else "Pasif")

    app.run(host="0.0.0.0", port=port, debug=debug)
