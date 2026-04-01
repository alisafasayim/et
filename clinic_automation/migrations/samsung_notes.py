"""
Samsung Notes -> Notion Migrasyon Aracı
========================================
Samsung Notes'tan dışa aktarılan notları (txt/pdf/sdoc)
Notion'a yapılandırılmış formatta aktarır.

Kullanım:
1. Samsung Notes'tan tüm notları dışa aktarın (Paylaş > Metin/PDF)
2. Notları bir klasöre koyun
3. Bu script ile Notion'a aktarın

Desteklenen formatlar: .txt, .pdf, .html, .sdoc
"""

import os
import re
import json
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from clinic_automation.config.settings import NotionConfig
from clinic_automation.modules.notion_client import NotionClient, NotionPatient

logger = logging.getLogger(__name__)


@dataclass
class SamsungNote:
    """Tek bir Samsung Notes kaydı."""
    file_path: str
    patient_name: str
    content: str
    date: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)
    note_type: str = "genel"  # genel, ilk_gorusme, kontrol, telefon


class SamsungNoteParser:
    """Samsung Notes dosyalarını parse eder."""

    # Yaygın hasta notu başlıkları
    HEADER_PATTERNS = [
        r"^(?:Hasta|Patient)\s*:\s*(.+)",
        r"^(?:İsim|Ad Soyad)\s*:\s*(.+)",
        r"^(\w+\s+\w+)\s*[-–]\s*(\d{1,2}[./]\d{1,2}[./]\d{2,4})",
        r"^(\d{1,2}[./]\d{1,2}[./]\d{2,4})\s*[-–]\s*(\w+\s+\w+)",
    ]

    NOTE_TYPE_KEYWORDS = {
        "ilk_gorusme": ["ilk görüşme", "ilk değerlendirme", "anamnez", "başvuru"],
        "kontrol": ["kontrol", "takip", "kontrol muayenesi"],
        "telefon": ["telefon görüşmesi", "telefonla", "aranıldı"],
    }

    def parse_file(self, file_path: str) -> SamsungNote:
        """Tek bir dosyayı parse eder."""
        path = Path(file_path)
        content = self._read_file(path)

        patient_name = self._extract_patient_name(content, path.stem)
        date = self._extract_date(content, path)
        note_type = self._detect_note_type(content)
        tags = self._extract_tags(content)

        return SamsungNote(
            file_path=file_path,
            patient_name=patient_name,
            content=content,
            date=date,
            tags=tags,
            note_type=note_type,
        )

    def parse_directory(self, directory: str) -> list[SamsungNote]:
        """Dizindeki tüm notları parse eder."""
        dir_path = Path(directory)
        if not dir_path.exists():
            raise FileNotFoundError(f"Dizin bulunamadı: {directory}")

        notes = []
        extensions = {".txt", ".html", ".pdf"}

        for file_path in sorted(dir_path.rglob("*")):
            if file_path.suffix.lower() in extensions:
                try:
                    note = self.parse_file(str(file_path))
                    notes.append(note)
                    logger.debug("Parse edildi: %s -> %s", file_path.name, note.patient_name)
                except Exception as e:
                    logger.error("Parse hatası: %s - %s", file_path.name, e)

        logger.info("%d Samsung Notes dosyası parse edildi.", len(notes))
        return notes

    def _read_file(self, path: Path) -> str:
        """Dosyayı okur (format tanıma ile)."""
        suffix = path.suffix.lower()

        if suffix == ".txt":
            return path.read_text(encoding="utf-8", errors="replace")
        elif suffix == ".html":
            return self._strip_html(path.read_text(encoding="utf-8", errors="replace"))
        elif suffix == ".pdf":
            return self._read_pdf(str(path))
        else:
            return path.read_text(encoding="utf-8", errors="replace")

    def _extract_patient_name(self, content: str, filename: str) -> str:
        """İçerikten veya dosya adından hasta adı çıkarır."""
        # Önce içerikten dene
        lines = content.strip().split("\n")
        for line in lines[:5]:  # İlk 5 satır
            for pattern in self.HEADER_PATTERNS:
                match = re.match(pattern, line.strip(), re.IGNORECASE)
                if match:
                    # Gruplardan isim olanı seç (tarih olmayan)
                    for group in match.groups():
                        if group and not re.match(r"\d{1,2}[./]", group):
                            return group.strip()

        # Dosya adından dene
        from clinic_automation.utils.helpers import extract_name_from_filename
        name = extract_name_from_filename(filename)
        if name:
            return name

        return "Bilinmeyen Hasta"

    def _extract_date(self, content: str, path: Path) -> Optional[datetime]:
        """İçerikten veya metadata'dan tarih çıkarır."""
        from clinic_automation.utils.helpers import extract_date_from_filename

        # Dosya adından
        date = extract_date_from_filename(path.name)
        if date:
            return date

        # İçerikten
        date_patterns = [
            r"(\d{1,2})[./](\d{1,2})[./](\d{4})",
            r"(\d{4})-(\d{2})-(\d{2})",
        ]
        for pattern in date_patterns:
            match = re.search(pattern, content[:500])
            if match:
                groups = match.groups()
                try:
                    if len(groups[0]) == 4:
                        return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                    else:
                        return datetime(int(groups[2]), int(groups[1]), int(groups[0]))
                except ValueError:
                    continue

        # Dosya değiştirilme tarihi (son çare)
        mtime = os.path.getmtime(str(path))
        return datetime.fromtimestamp(mtime)

    def _detect_note_type(self, content: str) -> str:
        """Not türünü içerikten tespit eder."""
        content_lower = content.lower()
        for note_type, keywords in self.NOTE_TYPE_KEYWORDS.items():
            if any(kw in content_lower for kw in keywords):
                return note_type
        return "genel"

    def _extract_tags(self, content: str) -> list[str]:
        """İçerikten etiketler çıkarır."""
        tags = []
        tag_patterns = [
            r"#(\w+)",
            r"(?:tanı|diagnosis)\s*:\s*(.+)",
            r"(?:ilaç|medication)\s*:\s*(.+)",
        ]
        content_lower = content.lower()
        for pattern in tag_patterns:
            matches = re.findall(pattern, content_lower)
            tags.extend(m.strip() for m in matches if len(m.strip()) > 2)
        return tags[:10]  # Maksimum 10 etiket

    @staticmethod
    def _strip_html(html: str) -> str:
        """HTML etiketlerini temizler."""
        clean = re.sub(r"<[^>]+>", "", html)
        clean = re.sub(r"&nbsp;", " ", clean)
        clean = re.sub(r"&amp;", "&", clean)
        clean = re.sub(r"&lt;", "<", clean)
        clean = re.sub(r"&gt;", ">", clean)
        return clean.strip()

    @staticmethod
    def _read_pdf(path: str) -> str:
        """PDF dosyasını metin olarak okur."""
        try:
            import subprocess
            result = subprocess.run(
                ["pdftotext", "-layout", path, "-"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: basit metin çıkarma
        try:
            with open(path, "rb") as f:
                content = f.read()
            # Basit metin çıkarma (PDF'den)
            text_parts = re.findall(rb"\(([^)]+)\)", content)
            return " ".join(p.decode("utf-8", errors="replace") for p in text_parts)
        except Exception:
            return f"[PDF okunamadı: {path}]"


class SamsungNoteMigrator:
    """Samsung Notes -> Notion migrasyon yöneticisi."""

    def __init__(self, notion_client: NotionClient):
        self.notion = notion_client
        self.parser = SamsungNoteParser()
        self.stats = {"total": 0, "migrated": 0, "skipped": 0, "errors": 0}

    def migrate_directory(
        self,
        directory: str,
        dry_run: bool = False,
    ) -> dict:
        """Dizindeki tüm Samsung Notes'u Notion'a aktarır."""
        notes = self.parser.parse_directory(directory)
        self.stats["total"] = len(notes)

        # Hasta adına göre grupla
        patient_notes: dict[str, list[SamsungNote]] = {}
        for note in notes:
            patient_notes.setdefault(note.patient_name, []).append(note)

        logger.info(
            "Migrasyon başlıyor: %d not, %d hasta",
            len(notes), len(patient_notes),
        )

        for patient_name, patient_note_list in patient_notes.items():
            # Tarihe göre sırala
            patient_note_list.sort(key=lambda n: n.date or datetime.min)

            if dry_run:
                logger.info(
                    "[DRY RUN] %s: %d not aktarılacak",
                    patient_name, len(patient_note_list),
                )
                self.stats["migrated"] += len(patient_note_list)
                continue

            try:
                self._migrate_patient_notes(patient_name, patient_note_list)
            except Exception as e:
                logger.error("Migrasyon hatası (%s): %s", patient_name, e)
                self.stats["errors"] += len(patient_note_list)

        logger.info(
            "Migrasyon tamamlandı: %d/%d başarılı, %d hata, %d atlandı",
            self.stats["migrated"], self.stats["total"],
            self.stats["errors"], self.stats["skipped"],
        )
        return self.stats

    def _migrate_patient_notes(
        self,
        patient_name: str,
        notes: list[SamsungNote],
    ) -> None:
        """Tek bir hastanın tüm notlarını aktarır."""
        # Notion'da hastayı bul veya oluştur
        patient = self.notion.get_or_create_patient(patient_name)

        for note in notes:
            try:
                self._migrate_single_note(patient, note)
                self.stats["migrated"] += 1
            except Exception as e:
                logger.error(
                    "Not aktarılamadı: %s - %s: %s",
                    patient_name, note.file_path, e,
                )
                self.stats["errors"] += 1

    def _migrate_single_note(
        self,
        patient: NotionPatient,
        note: SamsungNote,
    ) -> str:
        """Tek bir notu Notion'a aktarır."""
        date_str = note.date.strftime("%Y-%m-%d") if note.date else "Tarih Bilinmiyor"
        title = f"[Arşiv] {date_str} - {note.patient_name}"

        if note.note_type != "genel":
            type_labels = {
                "ilk_gorusme": "İlk Görüşme",
                "kontrol": "Kontrol",
                "telefon": "Telefon",
            }
            title += f" ({type_labels.get(note.note_type, note.note_type)})"

        # Seans sayfası oluştur
        page = self.notion.client.pages.create(
            parent={"database_id": self.notion.config.sessions_db_id},
            properties={
                "Başlık": {"title": [{"text": {"content": title}}]},
                "Hasta": {"relation": [{"id": patient.page_id}]},
                "Tarih": {"date": {"start": date_str}} if note.date else {},
            },
        )

        # İçeriği bloklar halinde ekle
        blocks = self._content_to_blocks(note.content)
        if blocks:
            # Notion API blok sınırı: 100
            for i in range(0, len(blocks), 100):
                chunk = blocks[i:i + 100]
                self.notion.client.blocks.children.append(
                    block_id=page["id"], children=chunk,
                )

        logger.info("Not aktarıldı: %s", title)
        return page["id"]

    @staticmethod
    def _content_to_blocks(content: str) -> list[dict]:
        """Metin içeriğini Notion bloklarına dönüştürür."""
        blocks = []
        lines = content.split("\n")

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Başlık tespiti
            if stripped.startswith("#"):
                level = min(len(stripped) - len(stripped.lstrip("#")), 3)
                text = stripped.lstrip("# ").strip()
                block_type = f"heading_{level}"
                blocks.append({
                    "object": "block",
                    "type": block_type,
                    block_type: {
                        "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
                    },
                })
            elif stripped.startswith(("- ", "* ", "• ")):
                text = stripped.lstrip("-*• ").strip()
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
                    },
                })
            elif re.match(r"^\d+[.)]\s", stripped):
                text = re.sub(r"^\d+[.)]\s*", "", stripped)
                blocks.append({
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
                    },
                })
            else:
                # Uzun satırları böl
                for chunk_start in range(0, len(stripped), 2000):
                    chunk = stripped[chunk_start:chunk_start + 2000]
                    blocks.append({
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": chunk}}]
                        },
                    })

        return blocks
