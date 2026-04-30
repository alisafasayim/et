"""
Pytest oturum ayarları.

Modüller `clinic/` kökünde flat yapılı (paket değil); tests/ alt
klasöründen import yapabilmek için clinic/ dizinini sys.path'e
ekliyoruz.
"""

import os
import sys
from pathlib import Path

CLINIC_DIR = Path(__file__).resolve().parent.parent
if str(CLINIC_DIR) not in sys.path:
    sys.path.insert(0, str(CLINIC_DIR))

# Test'ler dotenv yüklemesin — env'i her test kendi kontrol etsin.
os.environ.setdefault("CLINIC_TZ", "Europe/Istanbul")
