"""
DSM-5-TR Tanı Kodları ve Ölçek Skorlama
=========================================
Çocuk ve Ergen Psikiyatrisinde sık kullanılan tanılar ve
değerlendirme ölçekleri.
"""

from dataclasses import dataclass, field


@dataclass
class DSM5Diagnosis:
    code: str
    name_tr: str
    name_en: str
    category: str
    specifiers: list[str] = field(default_factory=list)


# Çocuk-Ergen Psikiyatrisinde sık kullanılan DSM-5-TR tanıları
DSM5_CODES: dict[str, DSM5Diagnosis] = {
    # Nörogelişimsel Bozukluklar
    "F90.0": DSM5Diagnosis("F90.0", "DEHB - Dikkat Eksikliği Baskın Tip", "ADHD - Predominantly Inattentive", "Nörogelişimsel",
                           ["Hafif", "Orta", "Ağır"]),
    "F90.1": DSM5Diagnosis("F90.1", "DEHB - Hiperaktivite/Dürtüsellik Baskın Tip", "ADHD - Predominantly Hyperactive-Impulsive", "Nörogelişimsel",
                           ["Hafif", "Orta", "Ağır"]),
    "F90.2": DSM5Diagnosis("F90.2", "DEHB - Birleşik Tip", "ADHD - Combined Presentation", "Nörogelişimsel",
                           ["Hafif", "Orta", "Ağır"]),
    "F84.0": DSM5Diagnosis("F84.0", "Otizm Spektrum Bozukluğu", "Autism Spectrum Disorder", "Nörogelişimsel",
                           ["Destek gerektiren", "Yoğun destek gerektiren", "Çok yoğun destek gerektiren"]),
    "F81.0": DSM5Diagnosis("F81.0", "Özgül Öğrenme Güçlüğü - Okuma", "Specific Learning Disorder - Reading", "Nörogelişimsel"),
    "F81.1": DSM5Diagnosis("F81.1", "Özgül Öğrenme Güçlüğü - Yazılı Anlatım", "Specific Learning Disorder - Written Expression", "Nörogelişimsel"),
    "F81.2": DSM5Diagnosis("F81.2", "Özgül Öğrenme Güçlüğü - Matematik", "Specific Learning Disorder - Mathematics", "Nörogelişimsel"),
    "F80.9": DSM5Diagnosis("F80.9", "İletişim Bozukluğu (BTA)", "Communication Disorder NOS", "Nörogelişimsel"),
    "F70": DSM5Diagnosis("F70", "Hafif Düzeyde Zihinsel Yetersizlik", "Mild Intellectual Disability", "Nörogelişimsel"),
    "F95.2": DSM5Diagnosis("F95.2", "Tourette Bozukluğu", "Tourette's Disorder", "Nörogelişimsel"),

    # Anksiyete Bozuklukları
    "F93.0": DSM5Diagnosis("F93.0", "Ayrılık Anksiyetesi Bozukluğu", "Separation Anxiety Disorder", "Anksiyete"),
    "F40.10": DSM5Diagnosis("F40.10", "Sosyal Anksiyete Bozukluğu", "Social Anxiety Disorder", "Anksiyete"),
    "F41.1": DSM5Diagnosis("F41.1", "Yaygın Anksiyete Bozukluğu", "Generalized Anxiety Disorder", "Anksiyete"),
    "F40.218": DSM5Diagnosis("F40.218", "Özgül Fobi", "Specific Phobia", "Anksiyete"),
    "F41.0": DSM5Diagnosis("F41.0", "Panik Bozukluğu", "Panic Disorder", "Anksiyete"),
    "F94.0": DSM5Diagnosis("F94.0", "Seçici Mutizm", "Selective Mutism", "Anksiyete"),
    "F42.2": DSM5Diagnosis("F42.2", "Obsesif-Kompulsif Bozukluk", "Obsessive-Compulsive Disorder", "OKB"),

    # Duygudurum Bozuklukları
    "F32.0": DSM5Diagnosis("F32.0", "Major Depresif Bozukluk - Tek Epizod, Hafif", "MDD - Single Episode, Mild", "Duygudurum"),
    "F32.1": DSM5Diagnosis("F32.1", "Major Depresif Bozukluk - Tek Epizod, Orta", "MDD - Single Episode, Moderate", "Duygudurum"),
    "F32.2": DSM5Diagnosis("F32.2", "Major Depresif Bozukluk - Tek Epizod, Ağır", "MDD - Single Episode, Severe", "Duygudurum"),
    "F34.1": DSM5Diagnosis("F34.1", "Süregiden Depresif Bozukluk (Distimi)", "Persistent Depressive Disorder", "Duygudurum"),
    "F34.81": DSM5Diagnosis("F34.81", "Yıkıcı Duygudurum Düzensizliği Bozukluğu", "Disruptive Mood Dysregulation Disorder", "Duygudurum"),
    "F31.9": DSM5Diagnosis("F31.9", "Bipolar Bozukluk (BTA)", "Bipolar Disorder NOS", "Duygudurum"),

    # Travma ve Stresle İlişkili
    "F43.10": DSM5Diagnosis("F43.10", "Travma Sonrası Stres Bozukluğu", "Post-Traumatic Stress Disorder", "Travma"),
    "F43.25": DSM5Diagnosis("F43.25", "Uyum Bozukluğu - Karışık", "Adjustment Disorder - Mixed", "Travma"),
    "F94.1": DSM5Diagnosis("F94.1", "Reaktif Bağlanma Bozukluğu", "Reactive Attachment Disorder", "Travma"),
    "F94.2": DSM5Diagnosis("F94.2", "Disinhibited Sosyal İlişki Bozukluğu", "Disinhibited Social Engagement Disorder", "Travma"),

    # Yıkıcı Davranış Bozuklukları
    "F91.1": DSM5Diagnosis("F91.1", "Davranım Bozukluğu - Çocukluk Başlangıçlı", "Conduct Disorder - Childhood Onset", "Davranış"),
    "F91.2": DSM5Diagnosis("F91.2", "Davranım Bozukluğu - Ergenlik Başlangıçlı", "Conduct Disorder - Adolescent Onset", "Davranış"),
    "F91.3": DSM5Diagnosis("F91.3", "Karşıt Olma Karşı Gelme Bozukluğu", "Oppositional Defiant Disorder", "Davranış"),

    # Yeme Bozuklukları
    "F50.01": DSM5Diagnosis("F50.01", "Anoreksiya Nervoza - Kısıtlayıcı Tip", "Anorexia Nervosa - Restricting", "Yeme"),
    "F50.02": DSM5Diagnosis("F50.02", "Anoreksiya Nervoza - Tıkınırcasına Yeme/Çıkarma Tipi", "Anorexia Nervosa - Binge-Eating/Purging", "Yeme"),
    "F50.2": DSM5Diagnosis("F50.2", "Bulimia Nervoza", "Bulimia Nervosa", "Yeme"),
    "F98.3": DSM5Diagnosis("F98.3", "Pika", "Pica", "Yeme"),

    # Boşaltım Bozuklukları
    "F98.0": DSM5Diagnosis("F98.0", "Enürezis", "Enuresis", "Boşaltım"),
    "F98.1": DSM5Diagnosis("F98.1", "Enkoprezis", "Encopresis", "Boşaltım"),

    # Uyku Bozuklukları
    "F51.01": DSM5Diagnosis("F51.01", "İnsomnia Bozukluğu", "Insomnia Disorder", "Uyku"),
    "G47.00": DSM5Diagnosis("G47.00", "Kabus Bozukluğu", "Nightmare Disorder", "Uyku"),
}


def search_diagnosis(query: str) -> list[DSM5Diagnosis]:
    """Tanı kodu veya isimle arama yapar."""
    query_lower = query.lower()
    results = []
    for code, diag in DSM5_CODES.items():
        if (query_lower in code.lower() or
            query_lower in diag.name_tr.lower() or
            query_lower in diag.name_en.lower() or
            query_lower in diag.category.lower()):
            results.append(diag)
    return results


def get_category_diagnoses(category: str) -> list[DSM5Diagnosis]:
    """Kategoriye göre tanıları getirir."""
    return [d for d in DSM5_CODES.values() if d.category.lower() == category.lower()]


# ────────────── Ölçek Skorlama ──────────────

@dataclass
class ScaleResult:
    """Ölçek skorlama sonucu."""
    scale_name: str
    total_score: int
    max_score: int
    severity: str
    subscale_scores: dict[str, int] = field(default_factory=dict)
    interpretation: str = ""
    percentile: float | None = None


class ScaleScorer:
    """Psikiyatrik değerlendirme ölçeklerini skorlar."""

    def score_conners_parent(self, responses: list[int]) -> ScaleResult:
        """Conners Ebeveyn Değerlendirme Ölçeği (Kısaltılmış Form).

        0=Hiçbir zaman, 1=Nadiren, 2=Sık sık, 3=Her zaman
        27 madde: [0-2] Dikkat Eksikliği, [3-8] Hiperaktivite,
                  [9-14] Karşıt Olma, [15-20] Bilişsel, [21-26] Anksiyete
        """
        total = sum(responses[:27]) if len(responses) >= 27 else sum(responses)
        max_score = 81

        subscales = {}
        if len(responses) >= 27:
            subscales["Dikkat Eksikliği"] = sum(responses[0:3])
            subscales["Hiperaktivite"] = sum(responses[3:9])
            subscales["Karşıt Olma"] = sum(responses[9:15])
            subscales["Bilişsel Sorunlar"] = sum(responses[15:21])
            subscales["Anksiyete"] = sum(responses[21:27])

        if total >= 60:
            severity = "Çok Yüksek"
        elif total >= 45:
            severity = "Yüksek"
        elif total >= 30:
            severity = "Orta"
        else:
            severity = "Normal"

        return ScaleResult(
            scale_name="Conners Ebeveyn Ölçeği (Kısa Form)",
            total_score=total,
            max_score=max_score,
            severity=severity,
            subscale_scores=subscales,
            interpretation=f"Toplam puan {total}/{max_score}: {severity} düzey.",
        )

    def score_cdi(self, responses: list[int]) -> ScaleResult:
        """Çocuklar İçin Depresyon Envanteri (CDI).

        27 madde, her biri 0-2 puan.
        """
        total = sum(responses[:27]) if len(responses) >= 27 else sum(responses)
        max_score = 54

        if total >= 25:
            severity = "Ağır Depresyon"
        elif total >= 19:
            severity = "Orta Depresyon"
        elif total >= 13:
            severity = "Hafif Depresyon"
        else:
            severity = "Normal"

        return ScaleResult(
            scale_name="Çocuklar İçin Depresyon Envanteri (CDI)",
            total_score=total,
            max_score=max_score,
            severity=severity,
            interpretation=f"Toplam puan {total}/{max_score}: {severity}. (Kesme puanı: 19)",
        )

    def score_scared(self, responses: list[int]) -> ScaleResult:
        """SCARED Çocuk Anksiyete Ölçeği.

        41 madde, 0-2 puan.
        """
        total = sum(responses[:41]) if len(responses) >= 41 else sum(responses)
        max_score = 82

        subscales = {}
        if len(responses) >= 41:
            subscales["Panik/Somatik"] = sum(responses[i] for i in [1, 6, 9, 12, 15, 18, 19, 22, 24, 27, 30, 34, 38])
            subscales["Yaygın Anksiyete"] = sum(responses[i] for i in [5, 7, 14, 21, 23, 28, 33, 35, 37])
            subscales["Ayrılık Anksiyetesi"] = sum(responses[i] for i in [4, 8, 13, 16, 20, 25, 29, 31])
            subscales["Sosyal Fobi"] = sum(responses[i] for i in [3, 10, 26, 32, 39, 40, 41] if i < len(responses))
            subscales["Okul Fobisi"] = sum(responses[i] for i in [2, 11, 17, 36] if i < len(responses))

        if total >= 25:
            severity = "Klinik Düzey Anksiyete"
        elif total >= 15:
            severity = "Sınırda"
        else:
            severity = "Normal"

        return ScaleResult(
            scale_name="SCARED Çocuk Anksiyete Ölçeği",
            total_score=total,
            max_score=max_score,
            severity=severity,
            subscale_scores=subscales,
            interpretation=f"Toplam puan {total}/{max_score}: {severity}. (Kesme puanı: 25)",
        )

    def score_sdq(self, responses: list[int]) -> ScaleResult:
        """Güçler ve Güçlükler Anketi (SDQ).

        25 madde, 0-2 puan. 5 alt ölçek x 5 madde.
        """
        total_difficulty = sum(responses[:25]) if len(responses) >= 25 else sum(responses)
        max_score = 40  # Prososyal hariç

        subscales = {}
        if len(responses) >= 25:
            subscales["Duygusal Sorunlar"] = sum(responses[i] for i in [3, 8, 13, 16, 24])
            subscales["Davranış Sorunları"] = sum(responses[i] for i in [5, 7, 12, 18, 22])
            subscales["Hiperaktivite"] = sum(responses[i] for i in [2, 10, 15, 21, 25] if i < len(responses))
            subscales["Akran Sorunları"] = sum(responses[i] for i in [6, 11, 14, 19, 23])
            subscales["Prososyal"] = sum(responses[i] for i in [1, 4, 9, 17, 20])
            total_difficulty = sum(v for k, v in subscales.items() if k != "Prososyal")

        if total_difficulty >= 20:
            severity = "Anormal"
        elif total_difficulty >= 16:
            severity = "Sınırda"
        else:
            severity = "Normal"

        return ScaleResult(
            scale_name="Güçler ve Güçlükler Anketi (SDQ)",
            total_score=total_difficulty,
            max_score=max_score,
            severity=severity,
            subscale_scores=subscales,
            interpretation=f"Toplam güçlük puanı {total_difficulty}/{max_score}: {severity}.",
        )
