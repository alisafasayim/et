"""
WhatsApp Chatbot - Niyet Tanıma ve Çok Adımlı Akışlar
======================================================
Doküman: whatsapp_otomasyon_sistemi_plani.md

Yetenekler:
- Niyet tanıma (randevu, iptal, ilaç, acil durum)
- Acil anahtar kelime tespiti -> anında bildirim
- Çok adımlı akışlar (hoş geldin, form takibi, kontrol)
- Güven skoru < 0.85 -> insan personele yönlendirme
- Mesaj saatleri: 08:00-21:00 (acil durum hariç)
"""

import re
import logging
from datetime import datetime, time as dtime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

from clinic_automation.config.settings import WhatsAppConfig, LLMConfig

logger = logging.getLogger(__name__)


class Intent(Enum):
    APPOINTMENT_QUERY = "randevu_sorgu"
    APPOINTMENT_CANCEL = "randevu_iptal"
    APPOINTMENT_CONFIRM = "randevu_onay"
    MEDICATION_QUESTION = "ilac_soru"
    EMERGENCY = "acil_durum"
    FORM_QUESTION = "form_soru"
    DOCTOR_CONTACT = "doktor_iletisim"
    GENERAL_QUESTION = "genel_soru"
    GREETING = "selamlama"
    THANKS = "tesekkur"
    OPT_OUT = "abonelik_iptal"
    UNKNOWN = "bilinmeyen"


@dataclass
class IntentResult:
    intent: Intent
    confidence: float  # 0.0 - 1.0
    entities: dict = field(default_factory=dict)
    raw_message: str = ""


@dataclass
class ConversationState:
    """Bir kullanıcı ile devam eden konuşma durumu."""
    phone: str
    patient_name: str = ""
    current_flow: str = ""  # welcome, form_reminder, appointment_confirm
    flow_step: int = 0
    context: dict = field(default_factory=dict)
    last_message_at: datetime = field(default_factory=datetime.now)
    message_count: int = 0


# Niyet tanıma kalıpları
INTENT_PATTERNS: dict[Intent, list[str]] = {
    Intent.APPOINTMENT_QUERY: [
        r"randevu.*(?:ne\s*zaman|saat|tarih|var\s*mı)",
        r"(?:sonraki|bir\s*sonraki|gelecek).*randevu",
        r"(?:kontrol|muayene).*(?:ne\s*zaman|tarih)",
    ],
    Intent.APPOINTMENT_CANCEL: [
        r"(?:randevu|kontrol).*(?:iptal|vazgeç|gelem)",
        r"(?:iptal|vazgeç).*(?:randevu|kontrol)",
        r"gelem(?:iy|ey)eceğ",
    ],
    Intent.APPOINTMENT_CONFIRM: [
        r"(?:randevu|kontrol).*(?:onay|tamam|gelec)",
        r"(?:onay|tamam|evet|gelec).*(?:randevu|kontrol)",
        r"^(?:evet|tamam|olur|geliyoruz|geleceğiz)$",
        r"evet\s+(?:geliyoruz|geleceğiz|geliriz|geldik|gelirim)",
    ],
    Intent.MEDICATION_QUESTION: [
        r"(?:ilaç|ilac|hap|şurup|damla).*(?:nasıl|ne\s*zaman|kaç|bitt|devam)",
        r"(?:reçete|recete).*(?:yenile|bitecek|bitmek)",
        r"(?:yan\s*etki|kusma|uyku|iştah)",
    ],
    Intent.EMERGENCY: [
        r"(?:acil|kriz|intihar|öldür|zarar)",
        r"(?:kendine|kendini).*(?:zarar|kıy)",
        r"(?:bayıl|nöbet|ateş.*yüksek)",
        r"(?:hastane|ambulans|112)",
    ],
    Intent.FORM_QUESTION: [
        r"form.*(?:nasıl|nerede|doldur|bulamı)",
        r"(?:anket|soru).*(?:doldur|nasıl)",
        r"link.*(?:çalışmı|açılmı)",
    ],
    Intent.DOCTOR_CONTACT: [
        r"doktor.*(?:görüş|konuş|ara|ulaş)",
        r"(?:görüş|konuş|ara).*doktor",
        r"(?:hekim|uzman).*(?:iste|rica|lütfen)",
    ],
    Intent.GREETING: [
        r"^(?:merhaba|selam|iyi\s*(?:gün|akşam|sabah)|hey)$",
        r"^(?:sa|selamün\s*aleyküm|as)$",
    ],
    Intent.THANKS: [
        r"(?:teşekkür|sağ\s*ol|eyvallah|mersi)",
    ],
    Intent.OPT_OUT: [
        r"(?:mesaj.*(?:atma|gönderme|istemiy))",
        r"(?:dur|durdur|çık|iptal.*abonelik)",
    ],
}

# Acil durum anahtar kelimeleri (her zaman tetiklenir)
EMERGENCY_KEYWORDS = [
    "intihar", "öldürmek", "ölmek istiyorum", "kendime zarar",
    "kendini öldür", "hap içtim", "bilek kestim", "acil kriz",
]

# Akış şablonları
FLOW_RESPONSES: dict[str, dict[int, str]] = {
    "welcome": {
        0: (
            "Hoş geldiniz! Ben {doctor_name} kliniğinin dijital asistanıyım.\n\n"
            "Size nasıl yardımcı olabilirim?\n"
            "1. Randevu bilgisi\n"
            "2. Form doldurma\n"
            "3. İlaç sorusu\n"
            "4. Doktor ile görüşme\n\n"
            "Numarayı yazabilir veya sorunuzu doğrudan sorabilirsiniz."
        ),
    },
    "form_reminder": {
        0: (
            "Sayın {patient_name} Velisi,\n\n"
            "Randevunuz öncesinde doldurmanız gereken formumuz henüz tamamlanmamış.\n"
            "Form linki: {form_url}\n\n"
            "Formu doldurdunuz mu?"
        ),
        1: (
            "Anlıyorum. Formun doldurulması değerlendirme sürecini hızlandıracaktır.\n"
            "Teknik bir sorun yaşıyorsanız '1', daha sonra dolduracaksanız '2' yazınız."
        ),
        2: (
            "Tamam, randevu gününe kadar formu doldurmanızı rica ederiz. "
            "Herhangi bir sorunuz olursa yazabilirsiniz."
        ),
    },
    "appointment_confirm": {
        0: (
            "Sayın {patient_name} Velisi,\n\n"
            "{date} tarihinde saat {time}'de randevunuz bulunmaktadır.\n\n"
            "Randevunuzu onaylıyor musunuz?\n"
            "1. Evet, geleceğiz\n"
            "2. İptal etmek istiyorum\n"
            "3. Tarih değişikliği istiyorum"
        ),
    },
    "post_session": {
        0: (
            "Sayın {patient_name} Velisi,\n\n"
            "Bugünkü görüşmemiz için teşekkür ederiz.\n\n"
            "Bir sonraki kontrolünüz: {next_date}\n"
            "{medication_info}\n\n"
            "Herhangi bir sorunuz olursa bu numaradan ulaşabilirsiniz.\n"
            "Sağlıklı günler dileriz."
        ),
    },
}


SMART_RESPONSE_SYSTEM_PROMPT = """Sen bir çocuk ve ergen psikiyatrisi kliniğinin WhatsApp asistanısın.
Hasta velileriyle yazışırsın. Görevin: kısa, net, şefkatli, klinik açıdan güvenli yanıtlar vermek.

KURALLAR:
- ASLA tanı koymazsın, ilaç dozu önermezsin, tıbbi yorum yapmazsın.
- Cevabın TÜRKÇE ve EN FAZLA 3 CÜMLE olsun. WhatsApp mesajı gibi kısa yaz.
- Acil durum (intihar, kendine zarar, kriz) belirtisi görürsen HEMEN 112'ye yönlendir.
- Hasta dosyasından sadece GEREKLİ bilgiyi kullan; aileye dosya içeriğini okuma.
- Emin değilsen "Doktora ileteceğim, kısa sürede dönüş yapacağız" de.
- Randevu/form/ilaç soruları için somut yanıt ver; bilinmiyorsa personele yönlendir.
- İmza: mesajın sonuna ekleme yapma, kurumsal şablon zorla kullanma.
"""


class ChatbotEngine:
    """WhatsApp chatbot niyet tanıma ve akış yönetimi."""

    def __init__(
        self,
        config: WhatsAppConfig,
        notion_client: Optional[Any] = None,
        llm_config: Optional[LLMConfig] = None,
    ):
        self.config = config
        self.conversations: dict[str, ConversationState] = {}
        self.notion = notion_client
        self.llm_config = llm_config
        self._llm_client = None  # lazy init

    def classify_intent(self, message: str) -> IntentResult:
        """Gelen mesajın niyetini sınıflandırır."""
        text = message.strip().lower()

        # Önce acil durum kontrolü (her zaman öncelikli)
        for keyword in EMERGENCY_KEYWORDS:
            if keyword in text:
                return IntentResult(
                    intent=Intent.EMERGENCY,
                    confidence=1.0,
                    raw_message=message,
                )

        # Niyet kalıplarını tara
        best_intent = Intent.UNKNOWN
        best_confidence = 0.0

        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    # Eşleşme uzunluğuna göre güven skoru
                    match_ratio = len(match.group()) / max(len(text), 1)
                    confidence = min(0.5 + match_ratio * 0.5, 0.99)
                    if confidence > best_confidence:
                        best_intent = intent
                        best_confidence = confidence

        return IntentResult(
            intent=best_intent,
            confidence=best_confidence,
            raw_message=message,
        )

    def process_message(
        self,
        phone: str,
        message: str,
        patient_name: str = "",
    ) -> str:
        """Gelen mesajı işler ve yanıt üretir."""
        # Mesaj saatleri kontrolü (acil durum hariç)
        intent_result = self.classify_intent(message)

        if not self._is_within_hours() and intent_result.intent != Intent.EMERGENCY:
            return (
                "Mesajınız alındı. Mesaj saatlerimiz 08:00-21:00 arasıdır. "
                "En kısa sürede dönüş yapılacaktır.\n\n"
                "ACİL durumda 112'yi arayınız."
            )

        # Konuşma durumunu al/oluştur
        state = self.conversations.get(phone)
        if not state:
            state = ConversationState(phone=phone, patient_name=patient_name)
            self.conversations[phone] = state

        state.last_message_at = datetime.now()
        state.message_count += 1

        # Hasta dosyasını yükle (Notion bağlıysa, henüz yüklenmediyse)
        patient_context = self._load_patient_context(state)

        # ACİL DURUM -> anında yönlendirme (+ doktora bildirim için log)
        if intent_result.intent == Intent.EMERGENCY:
            logger.warning("ACİL DURUM TESPİTİ: %s (%s) hasta=%s",
                           phone, message[:50], state.patient_name or "bilinmiyor")
            return self._handle_emergency(state)

        # Akıllı LLM yanıtı (Notion + Claude varsa):
        # UNKNOWN niyet veya düşük güven durumunda statik cevap yerine Claude kullan
        if self._can_use_smart_response():
            if (intent_result.intent == Intent.UNKNOWN or
                    intent_result.confidence < self.config.chatbot_confidence_threshold):
                smart = self._generate_smart_response(
                    state, message, intent_result, patient_context
                )
                if smart:
                    return smart

        # Güven skoru düşükse insana yönlendir
        if (intent_result.intent != Intent.UNKNOWN and
                intent_result.confidence < self.config.chatbot_confidence_threshold):
            return self._escalate_to_human(state, intent_result)

        # Aktif bir akış varsa devam et
        if state.current_flow:
            return self._continue_flow(state, message, intent_result)

        # Niyete göre yanıt
        handlers = {
            Intent.APPOINTMENT_QUERY: self._handle_appointment_query,
            Intent.APPOINTMENT_CANCEL: self._handle_appointment_cancel,
            Intent.APPOINTMENT_CONFIRM: self._handle_appointment_confirm,
            Intent.MEDICATION_QUESTION: self._handle_medication,
            Intent.FORM_QUESTION: self._handle_form_question,
            Intent.DOCTOR_CONTACT: self._handle_doctor_contact,
            Intent.GREETING: self._handle_greeting,
            Intent.THANKS: self._handle_thanks,
            Intent.OPT_OUT: self._handle_opt_out,
            Intent.UNKNOWN: self._handle_unknown,
        }

        handler = handlers.get(intent_result.intent, self._handle_unknown)
        return handler(state, intent_result)

    def start_flow(self, phone: str, flow_name: str, context: dict) -> str:
        """Proaktif bir akış başlatır (ör: hatırlatma, form takibi)."""
        state = self.conversations.get(phone)
        if not state:
            state = ConversationState(
                phone=phone,
                patient_name=context.get("patient_name", ""),
            )
            self.conversations[phone] = state

        state.current_flow = flow_name
        state.flow_step = 0
        state.context = context

        template = FLOW_RESPONSES.get(flow_name, {}).get(0, "")
        return template.format(**context) if template else ""

    def _continue_flow(self, state: ConversationState, message: str, intent: IntentResult) -> str:
        """Devam eden akışı ilerletir."""
        flow = state.current_flow
        step = state.flow_step + 1

        templates = FLOW_RESPONSES.get(flow, {})
        if step in templates:
            state.flow_step = step
            return templates[step].format(**state.context)
        else:
            # Akış tamamlandı
            state.current_flow = ""
            state.flow_step = 0
            return "Teşekkür ederiz. Başka sorunuz varsa yazabilirsiniz."

    def _handle_emergency(self, state: ConversationState) -> str:
        return (
            "DİKKAT: Mesajınız acil durum olarak algılandı.\n\n"
            "Acil yardım için:\n"
            "- 112 Acil Çağrı\n"
            "- 182 ALO Psikiyatri Hattı\n"
            "- En yakın acil servise başvurunuz\n\n"
            "Sağlık ekibimiz en kısa sürede sizinle iletişime geçecektir. "
            "Lütfen yalnız kalmayınız."
        )

    def _escalate_to_human(self, state: ConversationState, intent: IntentResult) -> str:
        logger.info(
            "İnsana yönlendirme: %s (niyet: %s, güven: %.2f)",
            state.phone, intent.intent.value, intent.confidence,
        )
        return (
            "Mesajınızı aldık. Sorunuzu en doğru şekilde yanıtlayabilmek için "
            "sağlık ekibimize iletiyoruz. En kısa sürede dönüş yapılacaktır.\n\n"
            "Acil durumda 112'yi arayınız."
        )

    def _handle_appointment_query(self, state: ConversationState, intent: IntentResult) -> str:
        return (
            "Randevu bilginizi kontrol ediyorum. "
            "Lütfen hastanın adını ve soyadını yazınız."
        )

    def _handle_appointment_cancel(self, state: ConversationState, intent: IntentResult) -> str:
        return (
            "Randevu iptal talebiniz alındı. "
            "İptal işlemi için sağlık ekibimiz sizinle iletişime geçecektir.\n\n"
            "Lütfen en az 24 saat önceden bilgi veriniz."
        )

    def _handle_appointment_confirm(self, state: ConversationState, intent: IntentResult) -> str:
        return "Randevu onayınız alındı. Randevu gününde görüşmek üzere. Sağlıklı günler!"

    def _handle_medication(self, state: ConversationState, intent: IntentResult) -> str:
        return (
            "İlaç ile ilgili sorunuzu doktorunuza iletiyoruz. "
            "Mesai saatleri içinde dönüş yapılacaktır.\n\n"
            "ÖNEMLİ: İlacınızı doktorunuza danışmadan kesmeyin veya dozunu değiştirmeyin."
        )

    def _handle_form_question(self, state: ConversationState, intent: IntentResult) -> str:
        return (
            "Form ile ilgili sorunuz için yardımcı olayım.\n\n"
            "- Form linki açılmıyorsa farklı bir tarayıcı deneyin\n"
            "- Form kaydetme sorunu varsa internet bağlantınızı kontrol edin\n"
            "- Sorun devam ederse 'DESTEK' yazın, sizi yönlendirelim."
        )

    def _handle_doctor_contact(self, state: ConversationState, intent: IntentResult) -> str:
        return (
            "Doktorunuzla görüşme talebiniz iletildi. "
            "Mesai saatleri içinde sizinle iletişime geçilecektir.\n\n"
            "Acil durumda 112'yi arayınız."
        )

    def _handle_greeting(self, state: ConversationState, intent: IntentResult) -> str:
        name = state.patient_name or ""
        greeting = f"Merhaba{' ' + name if name else ''}! "
        return greeting + (
            "Size nasıl yardımcı olabilirim?\n"
            "- Randevu bilgisi\n"
            "- Form sorusu\n"
            "- İlaç sorusu\n"
            "- Doktor ile görüşme"
        )

    def _handle_thanks(self, state: ConversationState, intent: IntentResult) -> str:
        return "Rica ederiz! Başka sorunuz olursa yazmaktan çekinmeyin. Sağlıklı günler!"

    def _handle_opt_out(self, state: ConversationState, intent: IntentResult) -> str:
        return (
            "Mesaj aboneliğiniz iptal edilmiştir. "
            "Tekrar aktifleştirmek isterseniz 'BAŞLA' yazabilirsiniz."
        )

    def _handle_unknown(self, state: ConversationState, intent: IntentResult) -> str:
        return (
            "Mesajınızı anlayamadım. Lütfen aşağıdakilerden birini seçin:\n"
            "1. Randevu\n"
            "2. Form\n"
            "3. İlaç\n"
            "4. Doktor ile görüşme\n\n"
            "Veya sorunuzu daha detaylı yazabilirsiniz."
        )

    # ─────────────────── Hasta Dosyası + Akıllı Yanıt ───────────────────

    def _load_patient_context(self, state: ConversationState) -> str:
        """
        Telefon numarasıyla Notion'dan hasta dosyasını çeker ve özet döner.
        Konuşma durumunda cacheler (her mesajda tekrar çekmemek için).
        """
        if not self.notion:
            return ""
        cached = state.context.get("_patient_summary")
        if cached is not None:
            return cached

        try:
            patient = self.notion.find_patient_by_phone(state.phone)
        except Exception as e:
            logger.warning("Hasta arama hatası: %s", e)
            state.context["_patient_summary"] = ""
            return ""

        if not patient:
            state.context["_patient_summary"] = ""
            return ""

        state.patient_name = patient.name
        summary = self.notion.get_patient_summary(patient)
        state.context["_patient_summary"] = summary
        return summary

    def _can_use_smart_response(self) -> bool:
        """Akıllı (LLM) yanıt kullanılabilir mi?"""
        return bool(self.llm_config and self.llm_config.anthropic_api_key)

    def _generate_smart_response(
        self,
        state: ConversationState,
        message: str,
        intent_result: "IntentResult",
        patient_context: str,
    ) -> Optional[str]:
        """
        Claude ile hasta bağlamını kullanarak akıllı yanıt üretir.
        Hata durumunda None döner (fallback'e düşer).
        """
        try:
            if self._llm_client is None:
                import anthropic
                self._llm_client = anthropic.Anthropic(
                    api_key=self.llm_config.anthropic_api_key
                )

            user_prompt_parts = []
            if patient_context:
                user_prompt_parts.append(
                    "HASTA DOSYASI:\n" + patient_context + "\n"
                )
            else:
                user_prompt_parts.append(
                    "NOT: Bu numara sistemde kayıtlı değil. "
                    "Kibarca kayıt olmalarını isteyin.\n"
                )

            user_prompt_parts.append(
                f"Tespit edilen niyet: {intent_result.intent.value} "
                f"(güven: {intent_result.confidence:.2f})\n"
            )
            user_prompt_parts.append(f"VELİ MESAJI: {message}\n")
            user_prompt_parts.append("\nLütfen kısa ve uygun bir WhatsApp yanıtı yaz.")

            response = self._llm_client.messages.create(
                model=self.llm_config.anthropic_model or "claude-sonnet-4-20250514",
                max_tokens=400,
                system=SMART_RESPONSE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": "".join(user_prompt_parts)}],
            )

            text = "".join(
                block.text for block in response.content
                if getattr(block, "type", "") == "text"
            ).strip()
            return text or None
        except Exception as e:
            logger.warning("LLM yanıtı üretilemedi, fallback kullanılıyor: %s", e)
            return None

    # ─────────────────── Yardımcılar ───────────────────

    def _is_within_hours(self) -> bool:
        """Mesaj saatleri içinde mi kontrolü."""
        now = datetime.now().time()
        start = dtime(self.config.messaging_hours_start, 0)
        end = dtime(self.config.messaging_hours_end, 0)
        return start <= now <= end
