#!/usr/bin/env bash
# Evolution API başlatıcı — Linux/macOS/Git Bash for Windows
#
# Kullanım:
#   chmod +x scripts/start_evolution.sh
#   ./scripts/start_evolution.sh

set -euo pipefail

cd "$(dirname "$0")/.."

# .env yüklü mü?
if [ ! -f .env ]; then
    echo "❌ .env bulunamadı. cp .env.example .env"
    exit 1
fi

source .env

# Required env vars
: "${EVOLUTION_API_KEY:?EVOLUTION_API_KEY .env'de boş — generate_secrets.py çalıştırın}"

# Docker daemon çalışıyor mu?
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker daemon kapalı."
    echo "   Windows: Docker Desktop'ı başlatın"
    echo "   Linux:   sudo systemctl start docker"
    exit 1
fi

# Veri dizini (KVKK: disk şifreleme yapılacak yer)
mkdir -p evolution_data evolution_data/logs

# Container başlat
echo "🐳 Evolution API başlatılıyor..."
docker compose -f docker-compose.evolution.yml up -d

# Healthy olmasını bekle
echo "⏳ Healthy olması bekleniyor (max 60s)..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8080/ > /dev/null 2>&1; then
        echo "✅ Evolution API çalışıyor: http://localhost:8080"
        echo ""
        echo "🔧 Sıradaki adımlar:"
        echo "   1. Tarayıcıda aç: http://localhost:8080/manager"
        echo "      → Login: ${EVOLUTION_API_KEY:0:8}..."
        echo "   2. 'New Instance' → Name: ${EVOLUTION_INSTANCE_NAME:-clinic}"
        echo "   3. Instance kartı → 'QR Code' → telefondan WhatsApp Web QR'i tara"
        echo "   4. Bağlantı 'open' olunca: python scripts/test_connections.py --only evolution"
        exit 0
    fi
    sleep 2
done

echo "⚠️ Evolution 60s içinde healthy olmadı. Loglar:"
docker compose -f docker-compose.evolution.yml logs --tail 50
exit 1
