"""
Klinik not şablonları - Notion sayfası ve düz metin formatları.
"""


def format_clinical_note_markdown(note) -> str:
    """Klinik notu Markdown formatında döndürür."""
    return f"""# Klinik Değerlendirme Notu
**Hasta:** {note.patient_name}
**Tarih:** {note.session_date}

---

## Başvuru Şikayeti
{note.chief_complaint}

## Öykü
{note.history_of_present}

## Ruhsal Durum Muayenesi (RDM)
{note.mental_status_exam}

## Gelişim Öyküsü
{note.developmental_history}

## Aile Öyküsü
{note.family_history}

## Tanı / Ön Tanı
{note.diagnosis}

## Tedavi Planı
{note.treatment_plan}

## İlaç Tedavisi
{note.medications}

## Kontrol / Takip Planı
{note.follow_up}

## Risk Değerlendirmesi
{note.risk_assessment}

{"## Ek Notlar" + chr(10) + note.additional_notes if note.additional_notes else ""}
"""


def format_clinical_note_plain(note) -> str:
    """Klinik notu düz metin olarak döndürür."""
    lines = [
        f"KLİNİK DEĞERLENDİRME NOTU",
        f"Hasta: {note.patient_name}",
        f"Tarih: {note.session_date}",
        "=" * 50,
        "",
        f"BAŞVURU ŞİKAYETİ: {note.chief_complaint}",
        "",
        f"ÖYKÜ: {note.history_of_present}",
        "",
        f"RUHSAL DURUM MUAYENESİ: {note.mental_status_exam}",
        "",
        f"GELİŞİM ÖYKÜSÜ: {note.developmental_history}",
        "",
        f"AİLE ÖYKÜSÜ: {note.family_history}",
        "",
        f"TANI: {note.diagnosis}",
        "",
        f"TEDAVİ PLANI: {note.treatment_plan}",
        "",
        f"İLAÇ TEDAVİSİ: {note.medications}",
        "",
        f"TAKİP PLANI: {note.follow_up}",
        "",
        f"RİSK DEĞERLENDİRMESİ: {note.risk_assessment}",
    ]

    if note.additional_notes:
        lines.extend(["", f"EK NOTLAR: {note.additional_notes}"])

    return "\n".join(lines)
