# GMH doküman yapısı: fotoğraflardan çıkarılanlar

Dış ağda yalnızca 9 ekran fotoğrafı görüldü (GMH: SIT test dokümanı, SSS, MKSD, SSDD). Bu dosya
gözlemleri ve bunlardan çıkan tasarım kararlarını özetler. **Doğrulanması gereken varsayımlar**
işaretlidir. İç ağda gerçek dosyalarla kontrol edilmeli (bkz. `IC_AG_ILK_PROMPT.md`, AŞAMA 1-2).

## 1. Dokümanlar ve sütunlar

| Doküman (dosya) | Ana sütun başlığı (= modül adı) | Tür sütunu | Bağlantı sütunları | Diğer önemli sütunlar |
|---|---|---|---|---|
| SIT test dokümanı (`aselfir-testdoc.htm`) | "CC SIT FLIR Procedure COMMON w FLIR" | Object Type | **SIT Link(s)** (ör. `FLIR-SSS-1103`), Links | Expected Results, CT.01.08 Formal Run (Start/End Time, Result, Redline, Typo, Finding(s) & Comment(s), Issue Tracker ID(s)) |
| SSS (`CC_SSS_FLIR_SSS.htm`) | "FLIR SSS" | **Quality** | SPS Link ID, Out-links (T-70 ... SPS), **Links** (bağlı test ve SSDD nesneleri, metinleriyle) | Configuration, Requirement Source, Rationale, Verification Method/Phase/Level/Evidence, Qualification Approval Status, Risk, Notes |
| MKSD (`CC_MKSD_CC_MKSD.htm`) | modül adı (MFD/KDU ekran tanımı) | Quality | **All Out Links** (ör. `CC-SSS-1664 CC-SSS-2106`) | Configuration, MGSY_SysPCR, Requirement Source, Rationale, Allocation, TSI Ref. |
| SSDD (`CC_SSDD_CC_SSDD.htm`) | "IMAS System Subsystem Design Description" benzeri | Quality | All Out Links | Configuration, Allocation; hücre içinde ekipman tabloları |

ID'ler tireli ve öneklidir: `CC-SIT-FLIR-20`, `FLIR-SSS-45`, `CC-MKSD-12381321`, `CC-SSDD-17787`, `CC-SSS-1664`.
Önek, nesnenin hangi modüle ait olduğunu gösterir. İzlenebilirlik analizinde modül sayımı buna dayanır.

## 2. Nesne türleri

- Test dokümanı: **Test Case** ("1 FLIR Screen Objects Assignments") > Remark, Heading ("1.1 Requirements
  addressed", "1.2 Prerequisite conditions", "1.3 Test inputs", "1.4 Expected test results", ...) >
  **Test Part** ("Aircraft (A/C) Data Window") > **Test Precondition**, **Test Step**.
- SSS/MKSD/SSDD: Heading, Remark, Requirement, Table, Figure (OLE nesnesi, görsel), Figure Caption.

## 3. Test yazım üslubu (IFF örneklerinden farklı!)

Testler sinyal/simülatör satırı (`SIG : (2) NORMAL`) değil, **kokpit/HMI eylemi** biçiminde:

- Ön koşul: `On TUHP Simulator, Change Configuration : 1.with EGI`
- Adım: `On SMFD-3 and VMFD-4, Depress ON on "ON/OFF Toggle Switch"`,
  `On KDU-2, Depress EGI hard key. Depress LSK1 to set NAV SOL as GPS1.`
- Beklenen: `Verify that, On VMFD-1, SMFD-2, SMFD-3 and VMFD-4, FLIR page is observed.` ve madde
  işaretli alt kontroller: `• The color of the DCLT1 Soft Key Label is observed as Black on Cyan background.`
- Ekran/tuş adları kalın yazılmış. Madde işaretleri HTML'de `<img alt="Bullet point">` görseli
  (**varsayım: export'ta alt metni böyle**).

Sonuç: Ad sözlüğü (sinyal listesi) bu programda işe yaramaz. Doğru adlar (ekran, tuş, etiket, renk, sayfa)
MKSD/SSDD'den gelir. Otomatik değerlendirmede sinyal ölçütleri boş kalır. Bu yüzden **terim recall/precision**
eklendi (SMFD-3, LSK1, DCLT1, "HDG REF" gibi adların karşılaştırması).

## 4. İzlenebilirlik zinciri (görülen)

```text
SIT Test Step --SIT Link(s)--> SSS Requirement (FLIR-SSS-...)
SSS Requirement --Links--> SSDD nesneleri (CC-SSDD-273 ...)  ve test nesneleri (CC-SIT-FLIR-...)
SSS Requirement --Out-links / SPS Link ID--> SPS (üst seviye)
MKSD Requirement --All Out Links--> SSS (CC-SSS-...)        (geri bağlantı: MKSD -> SSS)
```

Araç bu zinciri kullanır. Hedef gereksinimin bağlantı sütunlarındaki ID'ler ve gereksinime geri bağlantı
veren doküman nesneleri, benzerlik aramasından **önce** prompt'a "TRACE-LINKED DOCUMENT OBJECTS" olarak
eklenir (`knowledge.follow_links`, `link_depth`, `max_linked`).

Dikkat: MKSD, `CC-SSS-...` (ortak SSS) nesnelerine bağlanıyor. FLIR SSS (`FLIR-SSS-...`) ile ortak SSS
arasındaki ilişki fotoğraflardan anlaşılmıyor. Ortak SSS de docs'a eklenirse ve `link_depth: 2` olursa
FLIR-SSS -> CC-SSS -> MKSD zinciri yakalanabilir. **İç ağda kontrol edilmeli.**

## 5. Testçilerin muhtemelen baktığı diğer dokümanlar (doğrulanmalı)

Fotoğraflardaki ipuçlarından:

- **Test ortamı / simülatör dokümanı**: "TUHP Simulator, Change Configuration : 1.with EGI". Konfigürasyon
  numaraları ve adları bir test ortamı tanım dokümanından geliyor olmalı.
- **Konfigürasyon tanımı**: Configuration sütunu (Common, DGF, "Common w FLIR", "DGF w TACAN"). Testin
  hangi konfigürasyonda koşacağını belirliyor.
- **SPS** (Out-links, SPS Link ID): üst sistem gereksinimleri; gerekçe ve bağlam.
- **ICD/IDD**: arayüz ve veri tanımları (NAV SOL GPS1/EGI gibi seçimler).
- **STP/SITP (test planı)**: "Requirements addressed / Prerequisite conditions / Test inputs / Expected test
  results" başlıkları bir plan şablonuna işaret ediyor; ortak ön koşullar orada olabilir.
- **Ortak (CC) dokümanlar**: CC-SSS, CC-MKSD, CC-SSDD. Bunlar programlar arası ortak; FLIR'a özgü olanlarla
  birlikte gerekebilir.

Hürkuş-2'de aynı roller farklı adlarla (GMFD, MKB ...) karşılanıyor. Rol eşlemesi `projects.json` içindeki
`docs[].role` alanıyla yapılır; koda isim yazılmaz.

## 6. Okuma açısından dikkat edilecekler (kodda karşılandı)

| Gözlem | Karşılık |
|---|---|
| Ana sütunun başlığı modül adı, sabit değil | Eşlenmemiş en uzun metinli sütun = ana sütun |
| Tür sütunu "Quality" | Takma ad + değerlerden tanıma (Heading/Requirement/Remark ... oranı) |
| Test dokümanında iki bağlantı sütunu | Gereksinimlere en çok çözülen sütun seçilir (`library` çıktısında `test_link_column`) |
| Bağlantı hücresinde ID + metin ("CC-SSDD-273 The default value ...") | Hücreden ID biçimli parçalar çıkarılır |
| Adım bazında bağlantı (bir Test Part birden çok gereksinimi doğruluyor) | Örnek ve karşılaştırmada yalnızca ilgili adımlar + ön koşullar kullanılır |
| Gereksinimin "Links" sütununda bağlı testlerin metni var | Bağlantı sütunları prompt'a hiçbir zaman yazılmaz (sızıntı) |
| Madde işareti görseli, OLE şekiller, hücre içi tablolar | "•", "[image: ...]" (yalnızca görselden oluşan nesne atlanır), "a \| b \| c" satırları |
| Formal run sütunları | Özellik olarak okunur, prompt'a girmez |
| Şekiller (OLE) okunamıyor | Sınır: yalnızca başlıkları (Figure Caption) kullanılabiliyor. Raporda belirtilmeli |
