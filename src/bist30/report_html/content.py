"""Static Turkish copy for the HTML report: rule-set steps, our deviations, labels.

Every ``quote`` marked ``verbatim=True`` is copied from ``docs/kural_seti.txt`` and
``tests/test_report_html.py`` checks that it appears on the stated PDF page
(``===== PAGE n =====`` marker). Table rows in the PDF text are garbled by the
extraction, so table quotes (``verbatim=False``) list their key tokens in
``check_tokens`` instead. Printed page = PDF page - 1.
"""

from __future__ import annotations

RULESET_TITLE = "BIST Piyasa Değeri Ağırlıklı Pay Endeksleri Kural Seti (Ağustos 2025)"
RULESET_FILE = "kural-seti-son-hali-2025.pdf"


def _ref(section: str, pdf_page: int | str) -> dict:
    if isinstance(pdf_page, str):  # range "14–15"
        a, b = (int(x) for x in pdf_page.split("–"))
        printed = f"{a - 1}–{b - 1}"
    else:
        printed = str(pdf_page - 1)
    return {"section": section, "pdf_page": str(pdf_page), "printed_page": printed}


# Ordered: this IS the calculation sequence (scope -> inputs -> formula -> divisor -> timing).
RULE_STEPS: list[dict] = [
    {
        "key": "kapsam",
        "title": "Hangi 30 hisse?",
        "refs": [_ref("3.3", 12), _ref("2.16", 7), _ref("2.17", 8)],
        "quotes": [
            {"ref": "3.3", "pdf_page": 12, "verbatim": True,
             "text": "Yıldız Pazar’da işlem gören şirketler arasından seçilen 30 paydan oluşur."},
            {"ref": "2.17", "pdf_page": 8, "verbatim": True,
             "text": "Endeks Dönemi, endekslerin kapsamına ilişkin yapılan periyodik gözden geçirme sonucunda "
                     "endekslerin kapsamında yapılacak dönemsel değişikliklerin geçerli olacağı dönemi ifade eder."},
            {"ref": "2.16 (tablo)", "pdf_page": 7, "verbatim": False,
             "text": "BIST 30 · Kullanılan veri: Pay Sayısı, FDPO · Değerleme günü: Kasım, Şubat, Mayıs ve "
                     "Ağustos aylarının son günü",
             "check_tokens": ["BIST 30 Pay Sayısı, Kasım, Şubat, Mayıs ve Ağustos aylarının son", "FDPO günü"]},
        ],
        "plain": "Endeks üç aylık dönemlerle çalışır. Ekim–Aralık 2026 dönemi listesi Ağustos sonu verisiyle "
                 "belirlendi ve 01.10.2026'da yürürlüğe girdi. Dönem içinde liste sabittir.",
        "ours": "Bileşen listesini Borsa İstanbul'un Pay Endeks Raporu'ndan (hisse_endeks_ds.csv) ve "
                "günlük bültendeki BIST 30 üyelik bayrağından alıyoruz.",
    },
    {
        "key": "pay-sayisi",
        "title": "Pay sayısı (N)",
        "refs": [_ref("2.2", 6)],
        "quotes": [
            {"ref": "2.2", "pdf_page": 6, "verbatim": True,
             "text": "Şirketlerin özsermaye hallerine ilişkin olarak Borsa İstanbul tarafından Kamuyu Aydınlatma "
                     "Platformu’nda (KAP– www.kap.org.tr) yapılan duyurular kapsamında ulaşılan şirket çıkarılmış "
                     "veya ödenmiş sermayeleri"},
        ],
        "plain": "N, şirketin çıkarılmış sermayesinin 1 TL nominal pay adedine çevrilmiş halidir.",
        "ours": "N değerlerini MKK'nın Fiili Dolaşım Raporu'ndaki ISIN bazında 'Toplam Pay Adedi' sütunundan "
                "alıyoruz. Çok sınıflı şirketlerde yalnız endekste işlem gören sınıfı kullanıyoruz (ör. ISCTR C, "
                "KRDMD D).",
    },
    {
        "key": "fdpo",
        "title": "Fiili dolaşım oranı (H) ve yuvarlama",
        "refs": [_ref("2.5", 6), _ref("2.19", 9), _ref("5.2.f", 15)],
        "quotes": [
            {"ref": "2.5", "pdf_page": 6, "verbatim": True,
             "text": "Fiili Dolaşımdaki Pay Oranı, Sermaye Piyasası Kurulu (SPK) tarafından, Borsa İstanbul "
                     "piyasalarında işlem gören payların halka açıklık oranlarını gösterir bir kavram olarak "
                     "tanımlanmış olup MKK tarafından hesaplanarak ilan edilmektedir."},
            {"ref": "2.19 (tablo)", "pdf_page": 9, "verbatim": False,
             "text": "Fiili Dolaşımdaki Pay Oranları: FDPO < %1 ise tam sayıdan sonra 2 hane, "
                     "FDPO >= %1 ise tam sayı",
             "check_tokens": ["FDPO < %1 ise tam sayıdan sonra 2 hane", "FDPO >= %1 ise tam sayı"]},
            {"ref": "5.2.f", "pdf_page": 15, "verbatim": True,
             "text": "%50 ve daha küçükse ve oranda 5 puan veya daha fazla bir değişim varsa"},
        ],
        "plain": "H, payların halka açık ve fiilen dolaşımdaki kısmıdır. Endekste yüzde olarak tam sayıya "
                 "yuvarlanır (ör. %32,26 → %32). MKK oranı her hafta değişir, ama endeks oranı yalnız eşik "
                 "aşılınca güncellenir (H ≤ %50 için 5 puan, H > %50 için 10 puan).",
        "ours": "Borsa'nın endeks FDPO bildirimi bulunan hisselerde o oranı kullanıyoruz. Bildirim bulunamayan "
                "hisselerde oranı MKK haftalık verisinden 5.2.f eşiklerine göre tahmin ediyoruz ve 2.19'a göre "
                "yuvarlıyoruz.",
    },
    {
        "key": "katsayi",
        "title": "Ağırlık katsayısı K = 1",
        "refs": [_ref("2.6", 6), _ref("2.21", 10)],
        "quotes": [
            {"ref": "2.6", "pdf_page": 6, "verbatim": True,
             "text": "Sınırlama yapılan ve payların endeks içindeki ağırlığı özel olarak belirlenen endekslerde, "
                     "payın fiili dolaşımda bulunan kısmının piyasa değeri bu katsayı ile çarpılarak dikkate alınır."},
            {"ref": "2.21 (tablo)", "pdf_page": 10, "verbatim": False,
             "text": "BIST 30 Ağırlık Sınırlamalı 10 · %10 · %15 / BIST 30 Ağırlık Sınırlamalı 25 · %25 · %30",
             "check_tokens": ["BIST 30 Ağırlık Sınırlamalı 10 %10 %15", "BIST 30 Ağırlık Sınırlamalı 25 %25 %30"]},
        ],
        "plain": "Sınırlama tablosunda yalnız BIST 30'un 'Ağırlık Sınırlamalı' versiyonları var. Düz BIST 30 "
                 "(XU030) sınırlamasızdır, bu yüzden her hisse için K = 1.",
        "ours": "Tüm bileşenlerde K = 1 kullanıyoruz.",
    },
    {
        "key": "formul",
        "title": "Formül",
        "refs": [_ref("4", 14)],
        "formula": "E_t = Σ (F_it / D_t) · N_it · H_it · K_it / B_t",
        "quotes": [
            {"ref": "4", "pdf_page": 14, "verbatim": True,
             "text": "Endeksler, kapsamlarında bulunan payların FDP Ağırlık Katsayılı PD’leri (Fiili Dolaşımdaki "
                     "Payların Ağırlık Katsayılı Piyasa Değeri) ile ağırlıklı olarak hesaplanır."},
            {"ref": "4", "pdf_page": 14, "verbatim": True,
             "text": "Bt = Endeksin t zamandaki bölen değeri"},
        ],
        "plain": "Her hisse için fiyat × pay sayısı × fiili dolaşım oranı × katsayı çarpılır, 30 hisse toplanır "
                 "ve bölene bölünür. TL endeksinde döviz kuru D = 1'dir.",
        "ours": "Her dakika sonunda her hissenin o ana kadarki son işlem fiyatını alıp aynı formülü uyguluyoruz.",
    },
    {
        "key": "bolen",
        "title": "Bölen ve düzeltme",
        "refs": [_ref("5.1", 14), _ref("5.2", "14–15"), _ref("2.13", 7)],
        "formula": "B_t+1 = (1 + ΔPD / PD_t) · B_t",
        "quotes": [
            {"ref": "5.1", "pdf_page": 14, "verbatim": True,
             "text": "Madde 5.2’de sayılan durumlar nedeniyle endeks formülünün payında değişiklik meydana"},
            {"ref": "5.2.a", "pdf_page": 14, "verbatim": True,
             "text": "Düzeltme temettü ödemeye başlama tarihinde, net temettü tutarı dikkate alınarak sadece "
                     "getiri endekslerinde yapılır."},
        ],
        "plain": "Endeksin payı fiyat hareketi dışında bir nedenle değişirse (FDPO değişikliği, bedelli artırım, "
                 "listeye giriş/çıkış) bölen düzeltilir ve endeks sıçramaz. Nakit temettü fiyat endeksinin "
                 "bölenini değiştirmez.",
        "ours": "Bölen değerini bir kez kalibre ediyoruz. Ondan sonra yalnız 5.1 formülüyle düzeltiyoruz. "
                "Bu dönemdeki tek düzeltme PETKM FDPO değişikliği (28.10.2026). Temettüler kayda geçiyor ama "
                "bölene dokunmuyor.",
    },
    {
        "key": "hassasiyet",
        "title": "Hassasiyet",
        "refs": [_ref("2.19", 9)],
        "quotes": [
            {"ref": "2.19 (tablo)", "pdf_page": 9, "verbatim": False,
             "text": "Endeks Değerleri: Tam sayıdan sonra 2 hane · Endeks Bölen Değerleri: Tam sayıdan sonra 8 hane",
             "check_tokens": ["Endeks Değerleri Tam sayıdan sonra 2 hane",
                              "Endeks Bölen Değerleri Tam sayıdan sonra 8 hane"]},
        ],
        "plain": "Endeks 2 ondalıkla, bölen 8 ondalıkla yayımlanır.",
        "ours": "Hesaplanan değeri 2, böleni 8 ondalığa yuvarlıyoruz. Ara toplam Σ F·N·H·K yuvarlanmıyor.",
    },
    {
        "key": "siklik",
        "title": "Hesaplama sıklığı",
        "refs": [_ref("2.18", 9)],
        "quotes": [
            {"ref": "2.18", "pdf_page": 9, "verbatim": True,
             "text": "Endeksler, Borsa İstanbul Pay Piyasası’nın açık olduğu zamanlarda hesaplanır."},
            {"ref": "2.18 (tablo)", "pdf_page": 9, "verbatim": False,
             "text": "BIST 30 · TL - Fiyat · Saniyede 1",
             "check_tokens": ["BIST 30 TL - Fiyat", "Saniyede 1"]},
        ],
        "plain": "Resmî XU030 seans boyunca saniyede bir hesaplanır.",
        "ours": "Ücretsiz veri 1 dakikalık bar olduğu için biz dakikada bir hesaplıyoruz ve resmî değerle yalnız "
                "aynı dakika sonunda karşılaştırıyoruz. En yakın zamanla eşleştirme yapmıyoruz.",
    },
]

# What we do differently / approximate, and why.
DIFFERENCES: list[dict] = [
    {"title": "Bölen bir kez kalibre edildi",
     "text": "Resmî bölen ücretsiz yayınlarda yok. Bu yüzden bölen B = Σ F·N·H·K ÷ E_resmî, ilk günün resmî "
             "kapanışında bir kez hesaplandı. Sonraki her dakika bağımsızdır: resmî seriye bir daha "
             "uydurma yapılmadı."},
    {"title": "As-of son işlem fiyatı",
     "text": "Her dakika sonunda, her hissenin o ana kadarki son işlem fiyatı kullanılır. Hiçbir zaman ileriye "
             "bakılmaz."},
    {"title": "15 dakika gecikmeli 1 dakikalık veri",
     "text": "Yahoo Finance verisi 15 dk gecikmeli gelir. Geriye dönük testte gecikme sonucu değiştirmez, "
             "ama çözünürlük saniye yerine dakikadır."},
    {"title": "Kapanış müzayedesi fiyatı yok",
     "text": "Yahoo 1 dakikalık veride tam günlerin son barı 17:59–18:00'dir. 18:00–18:05 kapanış müzayedesi "
             "fiyatı yoktur. Gün sonu bölen düzeltmesi ve fiyat limiti kontrolünde 'önceki kapanış' olarak "
             "son 1 dakikalık işlem fiyatı kullanılır. Bu durum bazı dakikaları yanlışlıkla 'fiyat limiti dışı' "
             "işaretleyebilir."},
    {"title": "Tazelik kuralı",
     "text": "Sürekli işlemde bir hissenin son işlemi 5 dakikadan eskiyse o dakika geçersiz sayılır. "
             "Değer yine hesaplanır ve grileştirilmiş olarak gösterilir, ama metriklere girmez."},
]

REASON_LABELS = {
    "stale": "eskimiş fiyat (>5 dk)",
    "missing_price": "fiyat yok",
    "outside_price_limit": "fiyat limiti dışı",
    "price_jump": "fiyat sıçraması (>%10)",
    "outside_session": "seans dışı",
    "divisor_adjustment_failed": "bölen düzeltmesi yapılamadı",
    "no_official": "resmî değer yok",
}

PHASE_LABELS = {
    "continuous": "sürekli işlem",
    "opening_auction": "açılış müzayedesi",
    "closing_auction": "kapanış müzayedesi",
    "closing_trades": "kapanış fiyatından işlem",
    "closed": "kapalı",
}


def reason_key(reason: str) -> str:
    """'stale>300s:AKBNK' -> 'stale'; 'price_jump>10%:X' -> 'price_jump'."""
    head = (reason or "").split(":", 1)[0]
    for k in REASON_LABELS:
        if head.startswith(k):
            return k
    return head
