#!/bin/bash
# =========================================================================
# Klinik Otomasyon - Hızlı Kurulum
# =========================================================================
# WhatsApp botunu minimum adımda ayağa kaldırır.
#
# Çalıştırma:
#   cd clinic_automation
#   bash scripts/quickstart.sh
# =========================================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

cd "$(dirname "$0")/.."

echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Klinik Otomasyon - Hızlı Kurulum                     ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ─────────────────── 1. Python ───────────────────
echo -e "${YELLOW}[1/6] Python kontrolü...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}HATA: python3 bulunamadı.${NC}"
    exit 1
fi
PY_VER=$(python3 --version)
echo -e "${GREEN}  ✓ $PY_VER${NC}"

# ─────────────────── 2. venv + bağımlılıklar ───────────────────
echo -e "${YELLOW}[2/6] Sanal ortam ve bağımlılıklar...${NC}"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo -e "${GREEN}  ✓ .venv oluşturuldu${NC}"
fi

source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo -e "${GREEN}  ✓ Bağımlılıklar yüklendi${NC}"

# ─────────────────── 3. .env ───────────────────
echo -e "${YELLOW}[3/6] .env dosyası...${NC}"
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "${GREEN}  ✓ .env oluşturuldu (.env.example'dan kopyalandı)${NC}"
    echo -e "${RED}  ⚠ ŞİMDİ .env dosyasını açıp API anahtarlarını doldurun!${NC}"
else
    echo -e "${GREEN}  ✓ .env zaten mevcut${NC}"
fi

# ─────────────────── 4. RSA anahtarları ───────────────────
echo -e "${YELLOW}[4/6] RSA şifreleme anahtarları...${NC}"
if [ ! -f ".rsa_key.pem" ]; then
    openssl genrsa -out .rsa_key.pem 4096 2>/dev/null
    openssl rsa -in .rsa_key.pem -pubout -out .rsa_key_pub.pem 2>/dev/null
    chmod 600 .rsa_key.pem
    echo -e "${GREEN}  ✓ 4096-bit RSA anahtar çifti oluşturuldu${NC}"
    echo -e "${YELLOW}  ⚠ .rsa_key.pem dosyasını GÜVENLİ bir yerde yedekleyin!${NC}"
else
    echo -e "${GREEN}  ✓ RSA anahtarları zaten mevcut${NC}"
fi

# ─────────────────── 5. Dizin yapısı ───────────────────
echo -e "${YELLOW}[5/6] Dizin yapısı...${NC}"
mkdir -p audio_files audio_files/review_queue logs
touch audit.log
echo -e "${GREEN}  ✓ Dizinler hazır${NC}"

# ─────────────────── 6. Durum kontrolü ───────────────────
echo -e "${YELLOW}[6/6] Durum kontrolü...${NC}"
python -c "
from dotenv import load_dotenv
load_dotenv()
import os
keys = {
    'Twilio SID': os.getenv('TWILIO_ACCOUNT_SID'),
    'Twilio Token': os.getenv('TWILIO_AUTH_TOKEN'),
    'Notion API': os.getenv('NOTION_API_KEY'),
    'Notion Patients DB': os.getenv('NOTION_PATIENTS_DB_ID'),
    'Anthropic': os.getenv('ANTHROPIC_API_KEY'),
    'OpenAI (Whisper)': os.getenv('OPENAI_API_KEY'),
}
missing = [k for k, v in keys.items() if not v]
if missing:
    print('  ⚠ EKSİK:', ', '.join(missing))
else:
    print('  ✓ Tüm anahtarlar doldurulmuş.')
" || true

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}Kurulum tamamlandı. Sonraki adımlar:${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${YELLOW}1) .env dosyasını doldurun (henüz yapmadıysanız):${NC}"
echo "   nano .env"
echo ""
echo -e "${YELLOW}2) Notion veritabanlarını oluşturun:${NC}"
echo "   python scripts/notion_setup.py --parent-page-id <PAGE_ID>"
echo "   (Çıktıdaki DB ID'lerini .env'e yapıştırın)"
echo ""
echo -e "${YELLOW}3) Webhook sunucusunu başlatın:${NC}"
echo "   python scripts/webhook_server.py"
echo ""
echo -e "${YELLOW}4) Başka bir terminalde ngrok çalıştırın (public URL için):${NC}"
echo "   ngrok http 5000"
echo ""
echo -e "${YELLOW}5) Twilio Console'da Webhook URL'ini ayarlayın:${NC}"
echo "   https://xxxx.ngrok-free.app/webhook/twilio"
echo ""
echo -e "${GREEN}Detaylı rehber: DEPLOY.md${NC}"
echo ""
