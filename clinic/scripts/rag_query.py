#!/usr/bin/env python3
"""
RAG Sorgu Testi — Bir mesaj gir, en yakın 3 Q&A çifti çek + Ollama draft cevap.

Pipeline:
1. Kullanıcı mesajı (ya CLI argüman ya stdin)
2. multilingual-e5-base ile embed
3. Chroma'da en yakın K=3 Q&A çek (cosine similarity)
4. Ollama (llama3.1) prompt: "geçmişte şu sorulara şöyle cevap verdin, bu yenisine ne dersin?"
5. Draft cevap çıktı → doktor onayı için (henüz otomatik gönderme YOK)

KVKK güvencesi:
- Embedding lokal (sentence-transformers)
- Chroma DB lokal (clinic/rag_db/)
- Ollama lokal (gpt-oss:20b veya llama3.1:latest)
- Hiçbir veri cloud'a gitmez

Kullanım:
    python scripts/rag_query.py "Merhaba doktor, çocuğum ödevini yapamıyor"
    python scripts/rag_query.py --top-k 5 "ilaç dozajı sorusu"
    python scripts/rag_query.py --no-llm "sadece benzer örnekleri göster"
"""

import argparse
import os
import sys
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

import requests

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

RAG_DB_DIR = Path(__file__).resolve().parent.parent / "rag_db"
COLLECTION_NAME = "whatsapp_qa_pairs"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:latest")  # draft cevap için
EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3")


RAG_PROMPT = """Sen Dr. Ali Safa Sayım'ın (Çocuk-Ergen Psikiyatrisi
Uzmanı) WhatsApp asistanısın. Doktorun yerine TASLAK cevap yazıyorsun.

DOKTORUN STİLİ (geçmiş 100+ mesajdan analiz):
- ÇOK KISA cevaplar (ortalama 1-2 cümle, 50-70 karakter)
- Çoğu cevap "Merhabalar" ile başlar
- "alalım" sıkça (ilaç onay), "müsaitseniz" (uygunluk teyidi)
- "kusura bakmayın" özürle başlar
- Resmi-samimi karışık, profesyonel ama mesafeli değil
- Asla uzun açıklama yapmaz, asla disclaimer'a kaçmaz

GEÇMİŞ ÖRNEKLER (DOKTORUN gerçek cevapları):
═══════════════════════════════════════════════════════════════
{examples}
═══════════════════════════════════════════════════════════════

YENİ HASTA SORUSU:
"{query}"

ZORUNLU KURALLAR:
1. ASLA "doktora danışın" YAZMA. Sen zaten doktorun asistanısın.
2. ASLA uzun disclaimer/uyarı YAZMA — doktorun stili kısa.
3. Geçmiş örneklere SADIK kal — orada yok ise spekülasyon YAPMA.
4. İlaç adı/dozaj UYDURMA. Geçmişte bu hasta için yazılmış ilaç varsa
   o stilde ("önceki reçete neydi alalım" gibi) sor.
5. Eğer benzer durum YOK ise sadece "Müsaitseniz arasak" veya
   "Anlatabilir misiniz" gibi kısa teyit isteği yaz — UZUN AÇIKLAMA YASAK.
6. Türkçe yaz, "Merhabalar" ile başla.
7. 1-2 cümle. Maksimum 100 karakter.

TASLAK CEVAP:"""


def get_ollama_embedding(text: str) -> list[float]:
    """Ollama API'dan tek bir text için embedding al."""
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBEDDING_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("embedding", [])


def query(text: str, top_k: int = 3) -> list[dict]:
    """Chroma'da en yakın K Q&A çifti çek."""
    import chromadb

    if not RAG_DB_DIR.exists():
        raise FileNotFoundError(
            f"RAG DB yok: {RAG_DB_DIR}\n"
            f"Önce: python scripts/build_rag_index.py"
        )

    query_emb = get_ollama_embedding(text)

    client = chromadb.PersistentClient(path=str(RAG_DB_DIR))
    collection = client.get_collection(name=COLLECTION_NAME)

    results = collection.query(
        query_embeddings=[query_emb],
        n_results=top_k,
    )

    hits = []
    for i in range(len(results["ids"][0])):
        hits.append({
            "id": results["ids"][0][i],
            "question": results["documents"][0][i],
            "answer": results["metadatas"][0][i].get("answer", ""),
            "chat": results["metadatas"][0][i].get("chat", "?"),
            "distance": results["distances"][0][i],
        })
    return hits


def call_ollama(prompt: str) -> str:
    """Ollama REST ile streaming çağrı."""
    response = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.5, "num_ctx": 4096},
        },
        timeout=600,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="Hasta mesajı")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--no-llm", action="store_true", help="Sadece benzer örnekler, LLM cevabı yok")
    args = parser.parse_args()

    if not args.query:
        # stdin'den oku
        query_text = sys.stdin.read().strip()
        if not query_text:
            print(f"{RED}✗ Sorgu boş{RESET}")
            return 1
    else:
        query_text = args.query

    print(f"{YELLOW}RAG Sorgu Testi{RESET}")
    print(f"{DIM}Hasta mesajı: {query_text[:200]}{RESET}\n")

    # 1. Chroma'da benzer Q&A çek
    print(f"{DIM}1) En yakın {args.top_k} Q&A çekiliyor...{RESET}")
    hits = query(query_text, top_k=args.top_k)
    print(f"{GREEN}✓ {len(hits)} eşleşme{RESET}\n")

    print(f"{YELLOW}━━━ Geçmiş Benzer Örnekler ━━━{RESET}")
    for i, h in enumerate(hits, 1):
        sim = 1 - h["distance"]
        q_preview = h["question"][:120].replace("\n", " ")
        a_preview = h["answer"][:120].replace("\n", " ")
        print(f"\n  {GREEN}#{i}{RESET} (similarity: {sim:.2f}, chat: {h['chat']})")
        print(f"  {DIM}Soru:{RESET} {q_preview}...")
        print(f"  {GREEN}Cevap:{RESET} {a_preview}...")

    if args.no_llm:
        return 0

    # 2. Ollama draft cevap
    print(f"\n{DIM}2) Ollama draft cevap üretiyor ({OLLAMA_MODEL})...{RESET}")
    examples = "\n".join([
        f"Örnek {i+1}:\n  Soru: {h['question'][:300]}\n  Cevap: {h['answer'][:400]}\n"
        for i, h in enumerate(hits)
    ])
    prompt = RAG_PROMPT.format(examples=examples, query=query_text)
    try:
        draft = call_ollama(prompt)
    except Exception as exc:
        print(f"{RED}✗ Ollama hatası: {exc}{RESET}")
        return 1

    print(f"\n{YELLOW}━━━ Draft Cevap (Doktor onaylasın){RESET}")
    print()
    print(draft)
    print()
    print(f"{DIM}─" * 60 + RESET)
    print(f"{YELLOW}!{RESET} Bu sadece TASLAK. Doktor mutlaka okuyup düzeltsin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
