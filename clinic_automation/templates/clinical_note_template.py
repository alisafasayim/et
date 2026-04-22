"""
Klinik not şablonları - Notion sayfası ve düz metin formatları.
"""


def format_clinical_note_markdown(note) -> str:
    """Klinik notu Markdown formatında döndürür."""
    sections = [
        f"# Klinik Değerlendirme Notu",
        f"**Hasta:** {note.patient_name}",
        f"**Tarih:** {note.session_date}",
        "",
        "---",
        "",
        f"## Başvuru Şikayeti\n{note.chief_complaint}",
        f"## Öykü\n{note.history_of_present}",
        f"## Ruhsal Durum Muayenesi (RDM)\n{note.mental_status_exam}",
        f"## Gelişim Öyküsü\n{note.developmental_history}",
        f"## Aile Öyküsü\n{note.family_history}",
        f"## Tanı / Ön Tanı\n{note.diagnosis}",
        f"## Tedavi Planı\n{note.treatment_plan}",
        f"## İlaç Tedavisi\n{note.medications}",
        f"## Kontrol / Takip Planı\n{note.follow_up}",
        f"## Risk Değerlendirmesi\n{note.risk_assessment}",
    ]

    if note.additional_notes:
        sections.append(f"## Ek Notlar\n{note.additional_notes}")

    if note.next_appointment:
        sections.append(f"## Sonraki Kontrol\n{note.next_appointment}")

    if note.family_report_requested:
        sections.append(f"## Rapor Talebi\n{note.family_report_details or 'Aile rapor talep etti.'}")

    if getattr(note, "current_medications", None):
        med_lines = []
        for med in note.current_medications:
            if isinstance(med, dict):
                line = med.get("name", "?")
                if med.get("dose"):
                    line += f" {med['dose']}"
                if med.get("frequency"):
                    line += f" ({med['frequency']})"
                med_lines.append(f"- {line}")
        if med_lines:
            sections.append("## Mevcut İlaçlar\n" + "\n".join(med_lines))

    if getattr(note, "referrals", None):
        sections.append("## Yönlendirmeler\n" + "\n".join(f"- {r}" for r in note.referrals))

    if getattr(note, "action_items", None):
        action_lines = []
        for item in note.action_items:
            line = f"- [ ] **{item.action_type}**: {item.description}"
            if item.deadline:
                line += f" _(süre: {item.deadline})_"
            action_lines.append(line)
        sections.append("## Aksiyonlar\n" + "\n".join(action_lines))

    return "\n\n".join(sections) + "\n"


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

    if note.next_appointment:
        lines.extend(["", f"SONRAKİ KONTROL: {note.next_appointment}"])

    if note.family_report_requested:
        lines.extend(["", f"RAPOR TALEBİ: {note.family_report_details or 'Evet'}"])

    if getattr(note, "current_medications", None):
        lines.append("")
        lines.append("MEVCUT İLAÇLAR:")
        for med in note.current_medications:
            if isinstance(med, dict):
                lines.append(f"  - {med.get('name', '?')} {med.get('dose', '')} {med.get('frequency', '')}")

    if getattr(note, "action_items", None):
        lines.append("")
        lines.append("AKSİYONLAR:")
        for item in note.action_items:
            lines.append(f"  [ ] [{item.action_type}] {item.description}")

    return "\n".join(lines)
