"""
Klinik Otomasyon Sistemi - Ana Orkestratör
==========================================
Tüm modülleri koordine eder ve CLI arayüzü sağlar.
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel

from clinic_automation.config.settings import get_config, AppConfig
from clinic_automation.modules.google_calendar import GoogleCalendarClient, Appointment
from clinic_automation.modules.google_forms import GoogleFormsClient
from clinic_automation.modules.whatsapp import WhatsAppAutomation
from clinic_automation.modules.transcription import AudioTranscriber
from clinic_automation.modules.audio_preprocessing import AudioPreprocessor
from clinic_automation.modules.smart_matcher import SmartMatcher, merge_partial_recordings
from clinic_automation.modules.clinical_notes import ClinicalNoteGenerator
from clinic_automation.modules.notion_client import NotionClient
from clinic_automation.modules.risk_assessment import RiskAssessor, RiskLevel, RISK_LABELS_TR
from clinic_automation.modules.patient_journey import JourneyManager, JourneyStage, Priority
from clinic_automation.modules.chatbot import ChatbotEngine
from clinic_automation.modules.dsm5_codes import search_diagnosis, ScaleScorer
from clinic_automation.migrations.samsung_notes import SamsungNoteMigrator
from clinic_automation.utils.security import EncryptionManager, AuditLogger
from clinic_automation.templates.clinical_note_template import (
    format_clinical_note_markdown,
)

console = Console()
logger = logging.getLogger(__name__)


def setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class ClinicAutomation:
    """Ana orkestratör sınıfı."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self.calendar = GoogleCalendarClient(self.config.google)
        self.forms = GoogleFormsClient(self.config.google)
        self.whatsapp = WhatsAppAutomation(self.config.whatsapp)
        self.transcriber = AudioTranscriber(self.config.transcription)
        self.preprocessor = AudioPreprocessor()
        self.note_generator = ClinicalNoteGenerator(self.config.llm)
        self.notion = NotionClient(self.config.notion)
        self.risk_assessor = RiskAssessor()
        self.journey_manager = JourneyManager()
        self.chatbot = ChatbotEngine(self.config.whatsapp)
        self.scale_scorer = ScaleScorer()
        self.encryption = EncryptionManager(
            self.config.security.encryption_key_path,
            self.config.security.rsa_key_path,
        )
        self.audit = AuditLogger(self.config.security.audit_log_path)

    def process_audio_files(
        self,
        audio_dir: Optional[str] = None,
        target_date: Optional[datetime] = None,
        dry_run: bool = False,
    ) -> list[dict]:
        """Ana iş akışı: ses dosyalarını işle ve Notion'a yaz.

        Adımlar:
        1. Ses dosyalarını bul ve ön-işle
        2. Calendar'dan randevuları çek
        3. Her dosyayı transkript et
        4. Akıllı eşleştirme yap
        5. Parçalı kayıtları birleştir
        6. Risk değerlendirmesi yap
        7. Klinik not üret
        8. Notion'a yaz (5 veritabanı)
        """
        results = []

        # 1. Ses dosyalarını bul
        audio_files = self.transcriber.get_audio_files(audio_dir)
        if not audio_files:
            console.print("[yellow]Ses dosyası bulunamadı.[/yellow]")
            return results

        # Daha önce işlenmiş dosyaları atla
        unprocessed = []
        for af in audio_files:
            done_marker = af.with_suffix(af.suffix + ".done")
            if done_marker.exists():
                logger.debug("Zaten işlenmiş, atlanıyor: %s", af.name)
            else:
                unprocessed.append(af)

        if not unprocessed:
            console.print("[yellow]Tüm ses dosyaları zaten işlenmiş. Yeniden işlemek için .done dosyalarını silin.[/yellow]")
            return results

        if len(unprocessed) < len(audio_files):
            console.print(f"[dim]{len(audio_files) - len(unprocessed)} dosya zaten işlenmiş, atlanıyor.[/dim]")

        audio_files = unprocessed
        console.print(f"\n[bold]{len(audio_files)} ses dosyası bulundu.[/bold]\n")

        # 1b. Ses ön-işleme (kalite kontrol, normalizasyon)
        console.print("[dim]Ses ön-işleme yapılıyor...[/dim]")
        preprocessed_map = {}
        for af in audio_files:
            try:
                analysis = self.preprocessor.analyze(str(af))
                if analysis.needs_preprocessing:
                    processed = self.preprocessor.preprocess(str(af))
                    preprocessed_map[str(af)] = {"path": processed, "quality": analysis.quality.value}
                else:
                    preprocessed_map[str(af)] = {"path": str(af), "quality": analysis.quality.value}

                if analysis.quality.value == "poor":
                    console.print(f"  [yellow]Düşük kalite: {af.name} (SNR: {analysis.snr_db:.1f}dB)[/yellow]")
            except Exception as e:
                logger.warning("Ön-işleme atlandı (%s): %s", af.name, e)
                preprocessed_map[str(af)] = {"path": str(af), "quality": "unknown"}

        # 2. Bilinen hastaları Notion'dan çek
        known_patients = []
        try:
            notion_patients = self.notion.get_all_patients()
            known_patients = [p.name for p in notion_patients]
            console.print(f"[dim]{len(known_patients)} kayıtlı hasta bulundu.[/dim]")
        except Exception as e:
            logger.warning("Notion hasta listesi alınamadı: %s", e)

        matcher = SmartMatcher(name_database=known_patients)

        # 3-4. Her dosyayı işle
        match_results = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Ses dosyaları işleniyor...", total=len(audio_files))

            for audio_file in audio_files:
                progress.update(task, description=f"İşleniyor: {audio_file.name}")

                try:
                    # İşlenmiş dosyayı kullan
                    process_info = preprocessed_map.get(str(audio_file), {})
                    process_path = process_info.get("path", str(audio_file))

                    # Transkripsiyon
                    transcription = self.transcriber.transcribe(process_path)
                    self.audit.log_data_access("audio", str(audio_file), "TRANSCRIBE")

                    # Calendar verisi
                    from clinic_automation.utils.helpers import extract_date_from_filename
                    file_date = extract_date_from_filename(audio_file.name)
                    appointments = []
                    if file_date:
                        try:
                            appointments = self.calendar.get_appointments(file_date)
                        except Exception as e:
                            logger.warning("Calendar verisi alınamadı: %s", e)

                    # Eşleştirme
                    match_result = matcher.match_audio(
                        transcription,
                        appointments,
                        audio_metadata={
                            "created_date": file_date,
                            "file_size": audio_file.stat().st_size,
                        },
                    )
                    match_results.append(match_result)

                except Exception as e:
                    logger.error("Dosya işlenemedi (%s): %s", audio_file.name, e)

                progress.advance(task)

        # 5. Parçalı kayıtları birleştir
        match_results = merge_partial_recordings(match_results)

        # 6-8. Risk değerlendirmesi + Klinik not üret + Notion'a yaz (5 DB)
        console.print(f"\n[bold]Klinik notlar üretiliyor...[/bold]\n")

        for match in match_results:
            for segment in match.patient_segments:
                try:
                    # Form verisi var mı kontrol et (çoklu form desteği)
                    form_data = None
                    try:
                        form_ids = self.config.google.form_ids
                        if form_ids:
                            form_data = self.forms.find_response_across_forms(
                                segment.patient_name, form_ids
                            )
                        elif self.config.google.form_id:
                            form_data = self.forms.find_response_for_patient(
                                segment.patient_name
                            )
                    except Exception:
                        pass

                    date_str = match.audio_date.strftime("%Y-%m-%d") if match.audio_date else "Bilinmiyor"

                    # Risk değerlendirmesi
                    risk = self.risk_assessor.assess_from_transcript(
                        segment.patient_name, segment.transcript_text, date_str
                    )
                    if risk.overall_level >= RiskLevel.HIGH:
                        console.print(
                            f"  [bold red]!! YÜKSEK RİSK: {segment.patient_name} "
                            f"({RISK_LABELS_TR[risk.overall_level]})[/bold red]"
                        )

                    if dry_run:
                        risk_label = RISK_LABELS_TR.get(risk.overall_level, "?")
                        console.print(
                            f"  [dim][DRY RUN] {segment.patient_name} - {date_str} "
                            f"(güven: {segment.confidence:.0%}, risk: {risk_label})[/dim]"
                        )
                        results.append({
                            "patient": segment.patient_name,
                            "date": date_str,
                            "confidence": segment.confidence,
                            "risk_level": risk_label,
                            "status": "dry_run",
                        })
                        continue

                    # Hasta dosyası bağlamını al (Notion'dan)
                    patient_ctx = ""
                    try:
                        existing = self.notion.find_patient(segment.patient_name)
                        if existing:
                            patient_ctx = self.notion.get_patient_summary(existing)
                    except Exception:
                        pass

                    # Klinik not üret
                    note = self.note_generator.generate(
                        segment, date_str, form_data, patient_context=patient_ctx
                    )

                    # Notion'a yaz - Hasta kaydı
                    patient = self.notion.get_or_create_patient(
                        segment.patient_name, form_data
                    )

                    # Notion'a yaz - Konsültasyon (klinik not)
                    page_id = self.notion.add_clinical_note(patient, note)

                    # Notion'a yaz - Ses kaydı metadata
                    audio_quality = preprocessed_map.get(match.audio_path, {}).get("quality", "")
                    try:
                        self.notion.add_audio_record(
                            patient=patient,
                            audio_path=match.audio_path,
                            session_date=date_str,
                            duration_seconds=segment.end_time - segment.start_time,
                            quality=audio_quality,
                            confidence=segment.confidence,
                            transcript_preview=segment.transcript_text[:500],
                        )
                    except Exception as e:
                        logger.warning("Ses kaydı DB'ye eklenemedi: %s", e)

                    # Notion'a yaz - Form yanıtı (varsa)
                    if form_data:
                        try:
                            self.notion.add_form_response(patient, form_data)
                        except Exception as e:
                            logger.warning("Form yanıtı DB'ye eklenemedi: %s", e)

                    self.audit.log_data_access(
                        patient.page_id, "clinical_note", "CREATE"
                    )

                    # İşlenmiş olarak işaretle + geçici dosyaları temizle
                    audio_path = Path(match.audio_path)
                    done_marker = audio_path.with_suffix(audio_path.suffix + ".done")
                    done_marker.touch()
                    processed_wav = audio_path.with_stem(audio_path.stem + "_processed").with_suffix(".wav")
                    if processed_wav.exists():
                        processed_wav.unlink()

                    # Sonuç göster
                    status = "needs_review" if match.needs_review else "success"
                    risk_label = RISK_LABELS_TR.get(risk.overall_level, "?")
                    console.print(
                        f"  [green]✓[/green] {segment.patient_name} - {date_str} "
                        f"(güven: {segment.confidence:.0%}, risk: {risk_label})"
                    )

                    results.append({
                        "patient": segment.patient_name,
                        "date": date_str,
                        "confidence": segment.confidence,
                        "risk_level": risk_label,
                        "page_id": page_id,
                        "status": status,
                        "note_preview": note.chief_complaint[:100],
                    })

                except Exception as e:
                    logger.error(
                        "Not oluşturulamadı (%s): %s",
                        segment.patient_name, e,
                    )
                    results.append({
                        "patient": segment.patient_name,
                        "date": match.audio_date.strftime("%Y-%m-%d") if match.audio_date else "?",
                        "status": "error",
                        "error": str(e),
                    })

        return results

    def send_reminders(self, days_ahead: int = 1) -> list[dict]:
        """Yaklaşan randevular için hatırlatma gönderir."""
        target_date = datetime.now() + timedelta(days=days_ahead)
        appointments = self.calendar.get_appointments(target_date)

        results = []
        for appt in appointments:
            if not appt.phone:
                logger.warning("Telefon numarası yok: %s", appt.patient_name)
                continue

            msg = self.whatsapp.send_appointment_reminder(
                patient_name=appt.patient_name,
                phone=appt.phone,
                appointment_time=appt.start_time,
            )
            results.append({
                "patient": appt.patient_name,
                "phone": appt.phone,
                "status": msg.status,
            })

        return results

    def sync_calendar(self, days: int = 7) -> int:
        """Son N günün randevularını Notion'a senkronize eder."""
        start = datetime.now() - timedelta(days=days)
        end = datetime.now() + timedelta(days=days)
        appointments = self.calendar.get_appointments_range(start, end)

        synced = 0
        for appt in appointments:
            try:
                self.notion.sync_appointment(appt)
                synced += 1
            except Exception as e:
                logger.error("Senkronizasyon hatası: %s - %s", appt.patient_name, e)

        return synced


# ─────────────────── CLI Komutları ───────────────────


@click.group()
@click.option("--debug", is_flag=True, help="Debug modunu etkinleştir.")
@click.pass_context
def cli(ctx, debug):
    """Klinik Otomasyon Sistemi - Çocuk ve Ergen Psikiyatrisi"""
    setup_logging(debug)
    ctx.ensure_object(dict)
    ctx.obj["config"] = get_config()
    ctx.obj["app"] = ClinicAutomation(ctx.obj["config"])


@cli.command()
@click.option("--dir", "audio_dir", help="Ses dosyaları dizini.")
@click.option("--date", "target_date", help="Hedef tarih (YYYY-MM-DD).")
@click.option("--dry-run", is_flag=True, help="Gerçek işlem yapmadan simüle et.")
@click.pass_context
def process(ctx, audio_dir, target_date, dry_run):
    """Ses dosyalarını işle, transkript et ve Notion'a yaz."""
    app: ClinicAutomation = ctx.obj["app"]

    date = None
    if target_date:
        date = datetime.strptime(target_date, "%Y-%m-%d")

    console.print(Panel(
        "[bold]Ses Dosyası İşleme[/bold]\n"
        f"Dizin: {audio_dir or 'varsayılan'}\n"
        f"Tarih: {target_date or 'tümü'}\n"
        f"Mod: {'Simülasyon' if dry_run else 'Gerçek'}",
        title="Klinik Otomasyon",
    ))

    results = app.process_audio_files(audio_dir, date, dry_run)

    # Özet tablo
    table = Table(title="İşlem Sonuçları")
    table.add_column("Hasta", style="cyan")
    table.add_column("Tarih")
    table.add_column("Güven", justify="right")
    table.add_column("Risk")
    table.add_column("Durum")

    for r in results:
        status_style = {
            "success": "[green]Başarılı[/green]",
            "needs_review": "[yellow]İnceleme Gerekli[/yellow]",
            "error": "[red]Hata[/red]",
            "dry_run": "[dim]Simülasyon[/dim]",
        }
        risk = r.get("risk_level", "?")
        risk_style = {"Kritik": "[bold red]Kritik[/bold red]", "Yüksek": "[red]Yüksek[/red]",
                       "Orta": "[yellow]Orta[/yellow]", "Düşük": "[green]Düşük[/green]"}.get(risk, risk)
        table.add_row(
            r["patient"],
            r.get("date", "?"),
            f"{r.get('confidence', 0):.0%}",
            risk_style,
            status_style.get(r["status"], r["status"]),
        )

    console.print(table)


@cli.command()
@click.option("--days", default=1, help="Kaç gün sonrası için hatırlatma.")
@click.pass_context
def remind(ctx, days):
    """Randevu hatırlatma mesajları gönder."""
    app: ClinicAutomation = ctx.obj["app"]
    results = app.send_reminders(days)
    console.print(f"[bold]{len(results)} hatırlatma gönderildi.[/bold]")
    for r in results:
        status = "[green]✓[/green]" if r["status"] == "sent" else "[red]✗[/red]"
        console.print(f"  {status} {r['patient']} ({r['phone']})")


@cli.command()
@click.option("--days", default=7, help="Senkronize edilecek gün aralığı.")
@click.pass_context
def sync(ctx, days):
    """Google Calendar'ı Notion ile senkronize et."""
    app: ClinicAutomation = ctx.obj["app"]
    count = app.sync_calendar(days)
    console.print(f"[bold green]{count} randevu senkronize edildi.[/bold green]")


@cli.command()
@click.argument("directory")
@click.option("--dry-run", is_flag=True, help="Simülasyon modu.")
@click.pass_context
def migrate(ctx, directory, dry_run):
    """Samsung Notes arşivini Notion'a aktar."""
    app: ClinicAutomation = ctx.obj["app"]
    migrator = SamsungNoteMigrator(app.notion)

    console.print(Panel(
        f"[bold]Samsung Notes Migrasyon[/bold]\n"
        f"Kaynak: {directory}\n"
        f"Mod: {'Simülasyon' if dry_run else 'Gerçek'}",
        title="Migrasyon",
    ))

    stats = migrator.migrate_directory(directory, dry_run)

    table = Table(title="Migrasyon Sonuçları")
    table.add_column("Metrik", style="cyan")
    table.add_column("Değer", justify="right")
    table.add_row("Toplam Not", str(stats["total"]))
    table.add_row("Aktarılan", f"[green]{stats['migrated']}[/green]")
    table.add_row("Atlanan", f"[yellow]{stats['skipped']}[/yellow]")
    table.add_row("Hata", f"[red]{stats['errors']}[/red]")
    console.print(table)


@cli.command()
@click.pass_context
def status(ctx):
    """Sistem durumunu kontrol et."""
    config = ctx.obj["config"]

    table = Table(title="Sistem Durumu")
    table.add_column("Modül", style="cyan")
    table.add_column("Durum")
    table.add_column("Detay")

    # Google API
    google_ok = Path(config.google.credentials_path).exists()
    table.add_row(
        "Google API",
        "[green]Hazır[/green]" if google_ok else "[red]Eksik[/red]",
        config.google.credentials_path,
    )

    # Notion
    notion_ok = bool(config.notion.api_key)
    table.add_row(
        "Notion API",
        "[green]Hazır[/green]" if notion_ok else "[red]Eksik[/red]",
        "API key " + ("ayarlandı" if notion_ok else "eksik"),
    )

    # WhatsApp
    wa_ok = bool(config.whatsapp.twilio_account_sid or config.whatsapp.evolution_api_url)
    table.add_row(
        "WhatsApp",
        "[green]Hazır[/green]" if wa_ok else "[red]Eksik[/red]",
        f"Sağlayıcı: {config.whatsapp.provider}",
    )

    # Transkripsiyon
    transcription_ok = bool(config.transcription.openai_api_key) or config.transcription.provider == "local"
    table.add_row(
        "Transkripsiyon",
        "[green]Hazır[/green]" if transcription_ok else "[red]Eksik[/red]",
        f"Sağlayıcı: {config.transcription.provider}",
    )

    # LLM
    llm_ok = bool(config.llm.anthropic_api_key or config.llm.openai_api_key)
    table.add_row(
        "LLM",
        "[green]Hazır[/green]" if llm_ok else "[red]Eksik[/red]",
        f"Sağlayıcı: {config.llm.provider}",
    )

    # Güvenlik
    table.add_row(
        "Şifreleme",
        "[green]Aktif[/green]" if config.security.encrypt_audio else "[yellow]Pasif[/yellow]",
        f"Anahtar: {config.security.encryption_key_path}",
    )

    console.print(table)


@cli.command()
@click.argument("query")
@click.pass_context
def dsm5(ctx, query):
    """DSM-5 tanı kodu ara. Örnek: clinic dsm5 'DEHB'"""
    results = search_diagnosis(query)
    if not results:
        console.print(f"[yellow]'{query}' için sonuç bulunamadı.[/yellow]")
        return

    table = Table(title=f"DSM-5 Arama: '{query}'")
    table.add_column("Kod", style="cyan")
    table.add_column("Tanı (TR)")
    table.add_column("Tanı (EN)")
    table.add_column("Kategori")

    for d in results:
        table.add_row(d.code, d.name_tr, d.name_en, d.category)

    console.print(table)


@cli.command()
@click.argument("directory")
@click.pass_context
def preprocess(ctx, directory):
    """Ses dosyalarını toplu ön-işle (normalizasyon, gürültü azaltma)."""
    app: ClinicAutomation = ctx.obj["app"]

    console.print(Panel(f"[bold]Ses Ön-İşleme[/bold]\nDizin: {directory}", title="Ön-İşleme"))

    results = app.preprocessor.batch_preprocess(directory)

    table = Table(title="Ön-İşleme Sonuçları")
    table.add_column("Dosya", style="cyan")
    table.add_column("Kalite")
    table.add_column("Durum")

    for r in results:
        quality_style = {"excellent": "[green]", "good": "[green]", "fair": "[yellow]", "poor": "[red]"}.get(r.get("quality", ""), "[dim]")
        status_style = {"processed": "[green]İşlendi[/green]", "skipped": "[dim]Atlandı[/dim]", "error": "[red]Hata[/red]"}.get(r["status"], r["status"])
        table.add_row(
            Path(r["file"]).name,
            f"{quality_style}{r.get('quality', '?')}[/]" if quality_style.startswith("[") else r.get("quality", "?"),
            status_style,
        )

    console.print(table)


@cli.command()
@click.pass_context
def audit(ctx):
    """Denetim kaydı bütünlüğünü doğrula."""
    app: ClinicAutomation = ctx.obj["app"]

    console.print("[bold]Denetim kaydı bütünlüğü kontrol ediliyor...[/bold]")
    is_valid, count = app.audit.verify_chain_integrity()

    if is_valid:
        console.print(f"[bold green]Bütünlük doğrulandı: {count} kayıt.[/bold green]")
    else:
        console.print(f"[bold red]BÜTÜNLÜK BOZUK! Doğrulanan: {count} kayıt.[/bold red]")


if __name__ == "__main__":
    cli()
