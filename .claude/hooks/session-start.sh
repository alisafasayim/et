#!/bin/bash
# SessionStart hook — Klinik repo'su Claude Code on the web oturumlarında
# pytest çalışabilsin diye hafif Python bağımlılıklarını kurar.
#
# - Sync mode (race condition riski yok)
# - Sadece web ortamında çalışır ($CLAUDE_CODE_REMOTE)
# - Idempotent (pip install tekrar tekrar güvenli — kurulu olanı atlar)
# - Ağır ML paketleri (faster-whisper, pyannote.audio, ollama) atlanır;
#   M1 timezone test'i importorskip ile zaten kendini geçer.

set -euo pipefail

# Yalnızca remote ortamda kur (lokal makinede no-op)
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

echo "[session-start] Klinik test bağımlılıkları kuruluyor..."

# Web container'ında --user flag'i hem virtualenv hem system Python'da
# çalışır. CI workflow ile aynı paket seti — 165 test geçer (sadece
# M1 timezone testi M1 ML deps olmadığında importorskip ile skip).
python3 -m pip install --quiet --user \
  python-dotenv \
  tenacity \
  cryptography \
  requests \
  flask \
  google-api-python-client \
  google-auth-httplib2 \
  google-auth-oauthlib \
  pytest \
  pytest-mock

echo "[session-start] Bağımlılıklar kuruldu."

# Pytest'in clinic/'ten import yapabilmesi için PYTHONPATH'a ekle.
# tests/conftest.py zaten path manipülasyonu yapıyor; bu fazladan
# güvence ve `pytest clinic/tests/...` doğrudan çağrımları için.
echo "export PYTHONPATH=\"$CLAUDE_PROJECT_DIR/clinic:\${PYTHONPATH:-}\"" >> "$CLAUDE_ENV_FILE"

echo "[session-start] Hazır."
