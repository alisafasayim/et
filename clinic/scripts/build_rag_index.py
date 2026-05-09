#!/usr/bin/env python3
"""
RAG Vektör DB İnşası — WhatsApp JSONL'den Q&A Çiftleri → Chroma Index

Pipeline:
1. JSONL dosyasını oku (whatsapp_messages_*.jsonl)
2. Her chat için mesajları kronolojik sırala
3. Q&A çiftleri çıkar: hasta sorusu (fromMe=false) → ardışık doktor cevabı (fromMe=true)
4. multilingual-e5-base ile her hasta sorusunu embed
5. Chroma'ya yaz (lokal, persistent)

Çıktı: clinic/rag_db/ klasöründe Chroma SQLite

Kullanım:
    python scripts/build_rag_index.py
    python scripts/build_rag_index.py --rebuild  # eski indexi sil, sıfırdan
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
RAG_DB_DIR = Path(__file__).resolve().parent.parent / "rag_db"
COLLECTION_NAME = "whatsapp_qa_pairs"

# Ollama embedding API (PyTorch dependency yok — sentence-transformers
# transformers 5.x ile PyTorch 2.4+ ister, bizde 2.1 var)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3")
EMBEDDING_DIM = 1024  # bge-m3 boyutu (nomic-embed-text 768)

# Q&A çift filtresi: çok kısa veya değersiz mesajları at
MIN_QUESTION_LEN = 10
MIN_ANSWER_LEN = 5
JUNK_PATTERNS = [
    "tamam", "teşekkür", "sağol", "sağ ol", "ok", "okay",
    "merhaba", "selam", "iyi günler", "iyi akşamlar",
    "evet", "hayır", "anladım", "peki",
]

# KLİNİK whitelist: hasta sorusunda bu kelimelerden EN AZ BİRİ varsa
# klinik diyalog say. Yoksa kişisel/asistan/aile mesajı, RAG'e alma.
CLINICAL_KEYWORDS = [
    # Hitap
    "hocam", "doktor", "dr.", "dr ",
    # İlaç/tedavi
    "ilaç", "ilaçlar", "doz", "tablet", "hap", "kapsül", "şurup",
    "concerta", "ritalin", "medikinet", "strattera", "prozac",
    "fluoksetin", "atomoksetin", "metilfenidat", "vitamin", "ferro",
    # Klinik durum
    "şikayet", "ağrı", "uyku", "iştah", "bulantı", "kusma",
    "hasta", "muayene", "kontrol", "rapor", "test", "psikiyatri",
    # Çocuk klinik
    "çocuğ", "kız", "oğul", "okul", "ders", "anaokul", "kreş",
    "öğretmen", "sınıf", "ödev", "dikkat", "davranış",
    # Semptomlar
    "kaygı", "korku", "panik", "üzgün", "mutsuz", "agresif",
    "hiperaktif", "tikler", "konuşma", "yürüme",
    # Randevu
    "randevu", "muayene", "seans", "kontrol", "anamnez",
    # Reçete/yedek
    "reçete", "doktor görüşmek", "ne zaman vereyim", "kaç kez",
    # Genel klinik
    "tedavi", "tanı", "teşhis", "yardım edin", "öneri",
]


def has_clinical_keyword(text: str) -> bool:
    """Mesaj klinik anahtar kelime içeriyor mu?"""
    t = text.lower()
    return any(kw in t for kw in CLINICAL_KEYWORDS)


def is_junk(text: str) -> bool:
    """Çok kısa ve standart mesaj mı? (eğitim için faydasız)"""
    t = text.lower().strip()
    if len(t) < MIN_QUESTION_LEN:
        return True
    # Sadece junk kelimelerden oluşuyor mu?
    if t in JUNK_PATTERNS:
        return True
    # Kısa + sadece tek junk kelimesinden ibaret
    if len(t) < 30 and any(t == p or t.startswith(p + " ") or t.endswith(" " + p) for p in JUNK_PATTERNS):
        return True
    return False


def extract_qa_pairs(jsonl_path: Path) -> list[dict]:
    """
    JSONL'den Q&A çiftleri çıkar.

    Algorithm:
    - Her chat için mesajları timestamp'e göre sırala
    - Ardışık [hasta soru, doktor cevap(lar)] gruplarını bul
    - Birden fazla hasta mesajı arka arkaya gelirse birleştir
    - Birden fazla doktor mesajı arka arkaya gelirse birleştir
    """
    # Chat → mesajlar
    by_chat = defaultdict(list)
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
                by_chat[m["chat_pseudonym"]].append(m)
            except Exception:
                continue

    qa_pairs = []
    for pseudonym, msgs in by_chat.items():
        # Kronolojik sırala
        msgs.sort(key=lambda m: m.get("timestamp", 0))

        # Sıralı: hasta(lar) → doktor(lar) → hasta(lar) → ...
        i = 0
        while i < len(msgs):
            # Hasta mesajları (fromMe=false) topla
            patient_chunk = []
            while i < len(msgs) and not msgs[i].get("fromMe"):
                body = (msgs[i].get("body") or "").strip()
                if body:
                    patient_chunk.append(body)
                i += 1

            # Sonraki doktor mesajları (fromMe=true) topla
            doctor_chunk = []
            while i < len(msgs) and msgs[i].get("fromMe"):
                body = (msgs[i].get("body") or "").strip()
                if body:
                    doctor_chunk.append(body)
                i += 1

            # Çift oluştur
            if patient_chunk and doctor_chunk:
                question = " ".join(patient_chunk)
                answer = " ".join(doctor_chunk)
                # Filter 1: junk değil + cevap min uzunluk
                if is_junk(question) or len(answer) < MIN_ANSWER_LEN:
                    continue
                # Filter 2: KLİNİK içerik whitelist — hasta sorusu klinik
                # anahtar kelime içermeli, yoksa kişisel/asistan mesajı
                if not has_clinical_keyword(question):
                    continue
                qa_pairs.append({
                    "chat_pseudonym": pseudonym,
                    "question": question,
                    "answer": answer,
                    "q_chars": len(question),
                    "a_chars": len(answer),
                })

    return qa_pairs


def get_ollama_embedding(text: str) -> list[float]:
    """Ollama API'dan tek bir text için embedding al."""
    import requests
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBEDDING_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("embedding", [])


def build_index(qa_pairs: list[dict], rebuild: bool = False) -> tuple[int, int]:
    """Chroma indexini inşa et. Döner: (eklendi, atlandı)"""
    import chromadb

    # RAG DB klasörü
    if rebuild and RAG_DB_DIR.exists():
        import shutil
        shutil.rmtree(RAG_DB_DIR)
        print(f"{YELLOW}  Eski index silindi{RESET}")
    RAG_DB_DIR.mkdir(exist_ok=True)

    # Chroma persistent
    client = chromadb.PersistentClient(path=str(RAG_DB_DIR))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # cosine similarity
    )

    print(f"{DIM}  Embedding modeli: {EMBEDDING_MODEL} (Ollama API){RESET}")
    print(f"{DIM}  Toplam {len(qa_pairs)} Q&A için embedding üretilecek{RESET}\n")

    # Ollama'da tek seferde 1 embed (REST API limit, batch yok). Sırayla.
    embeddings = []
    questions = [p["question"] for p in qa_pairs]
    for i, q in enumerate(questions):
        try:
            emb = get_ollama_embedding(q)
            embeddings.append(emb)
        except Exception as exc:
            print(f"{RED}  ✗ Embedding {i} hatası: {exc}{RESET}")
            # 0-vector fallback ki index bozulmasın
            embeddings.append([0.0] * EMBEDDING_DIM)
        if (i + 1) % 100 == 0:
            print(f"{DIM}  Embedding: {i+1}/{len(questions)}{RESET}")

    print(f"{GREEN}  ✓ {len(embeddings)} embedding üretildi{RESET}")

    # Chroma'ya batch insert
    ids = [f"qa_{i}" for i in range(len(qa_pairs))]
    documents = [p["question"] for p in qa_pairs]
    metadatas = [{
        "answer": p["answer"][:1500],  # Chroma metadata size limit
        "chat": p["chat_pseudonym"],
        "q_chars": p["q_chars"],
        "a_chars": p["a_chars"],
    } for p in qa_pairs]

    # Chroma 1000'lik batch'lerle insert
    insert_batch = 500
    for i in range(0, len(ids), insert_batch):
        collection.upsert(
            ids=ids[i:i + insert_batch],
            embeddings=embeddings[i:i + insert_batch],
            documents=documents[i:i + insert_batch],
            metadatas=metadatas[i:i + insert_batch],
        )

    return len(qa_pairs), 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="Eski indexi sil, sıfırdan")
    parser.add_argument(
        "--jsonl", default=None,
        help="JSONL dosyası (default: en son whatsapp_messages_*.jsonl)",
    )
    args = parser.parse_args()

    print(f"{YELLOW}RAG Vektör DB İnşası{RESET}")
    print(f"{DIM}Model: {EMBEDDING_MODEL} | DB: {RAG_DB_DIR}{RESET}\n")

    # JSONL bul
    if args.jsonl:
        jsonl_path = Path(args.jsonl)
    else:
        candidates = sorted(REPORTS_DIR.glob("whatsapp_messages_*.jsonl"))
        if not candidates:
            print(f"{RED}✗ JSONL yok. Önce: python scripts/export_whatsapp_messages.py{RESET}")
            return 1
        jsonl_path = candidates[-1]

    print(f"{DIM}1) JSONL: {jsonl_path.name}{RESET}")

    # Q&A çiftleri çıkar
    qa_pairs = extract_qa_pairs(jsonl_path)
    print(f"{GREEN}✓{RESET} {len(qa_pairs)} Q&A çifti çıkarıldı")

    if not qa_pairs:
        print(f"{YELLOW}Hiç çift yok, çıkıyor.{RESET}")
        return 0

    # Örnek istatistik
    avg_q = sum(p["q_chars"] for p in qa_pairs) / len(qa_pairs)
    avg_a = sum(p["a_chars"] for p in qa_pairs) / len(qa_pairs)
    print(f"{DIM}  Ortalama: soru {avg_q:.0f} char, cevap {avg_a:.0f} char{RESET}\n")

    # İlk 3 örnek
    print(f"{DIM}  Örnekler:{RESET}")
    for p in qa_pairs[:3]:
        q_preview = p["question"][:60].replace("\n", " ")
        a_preview = p["answer"][:60].replace("\n", " ")
        print(f"{DIM}    Q: {q_preview}...{RESET}")
        print(f"{DIM}    A: {a_preview}...{RESET}")
        print()

    # Index inşası
    print(f"{DIM}2) Embedding + Chroma index inşası{RESET}")
    added, _ = build_index(qa_pairs, rebuild=args.rebuild)

    print()
    print(f"{YELLOW}━━━ Tamamlandı ━━━{RESET}")
    print(f"  Q&A çiftleri: {added}")
    print(f"  Chroma DB: {RAG_DB_DIR}")
    print(f"  Boyut: {sum(f.stat().st_size for f in RAG_DB_DIR.rglob('*') if f.is_file()) // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
