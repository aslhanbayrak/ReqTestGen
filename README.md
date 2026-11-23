# ReqTestGen: DOORS gereksinimlerinden test prosedürü taslağı

DOORS'tan **HTML** olarak alınan gereksinim modülünü okur. Her gereksinim için kurumun mevcut test
dokümanlarının üslubunda test case'leri (ön koşul + adım + beklenen sonuç) üretir. Sonucu **HTML** test
dokümanı olarak yazar. Gerçek testi olan projelerde üretilenleri gerçeklerle karşılaştırıp ölçer.

İç ağdaki yapay zekayla devam etmek için: **`IC_AG_ILK_PROMPT.md`** (ilk prompt + görev planı).
Kod mimarisi: `AGENTS.md`. Fotoğraflardan çıkarılan GMH doküman yapısı: `docs/GMH_DOKUMAN_ANALIZI.md`.

## Hızlı başlangıç (komut yazmadan)

1. **İç ağa taşıma:** Dış ağda `baslat.bat` > **8** ile zip paketi oluştur (anahtar, veri, sonuçlar
   pakete girmez). Zip'i iç ağdaki bilgisayarda bir klasöre aç.
2. **Python:** Bilgisayarda Python 3.9+ varsa bir şey yapma. Yoksa python.org'daki "Windows embeddable
   package (64-bit)" zip'ini klasörün içinde `python\` adlı klasöre aç (`python\python.exe` olacak şekilde).
   Kurulum ve ek paket gerekmez.
3. **Dokümanlar:** DOORS HTML export'larını program başına bir klasöre koy:
   `data\GMH\...htm`, `data\HURKUS2\...htm`. Programlar arası ortak dokümanlar için `data\ORTAK\`.
   Dosya adları önemli değil.
4. **`baslat.bat`'a çift tıkla** ve menüden seç:
   - **1** Kurulum: LLM adresi ve anahtar sorulur, modeller sunucudan listelenir, numarayla seçilir, bağlantı denenir.
   - **7** Tam otomatik: dokümanları tarar, projeleri kurar, testli projelerde 10 gereksinimle doğrulama
     yapar, testi olmayan (yeni) projeler için test üretir ve raporları açar.
   - Ayrı ayrı: **2** tara, **3** doğrula, **4** test üret, **5** modele giden metni gör, **6** son rapor.

Tarama (2) her program klasöründe şunları kendisi bulur:
- Test Step nesnesi olan dosya test dokümanıdır.
- Bağlantılarının en çok gittiği dosya gereksinim modülüdür.
- Kalanlar yardımcı dokümandır.

Testi olmayan bir programda gereksinim modülü tahmin edilir ve menü onay ister. Sonuç `projects.json`'a
yazılır; elle de düzenlenebilir (doküman rolleri, filtreler). Doğrulamada bir projeye kendi programından
örnek gösterilmez. Böylece ölçüm, "yeni bir program geldiğinde ne olur" sorusunu dürüstçe cevaplar.

## Nasıl çalışır (RAG, model eğitimi yok)

Her gereksinim için modele tek bir prompt gider. Prompt şu parçalardan oluşur:

1. **Örnekler**: Diğer projelerdeki en benzer gereksinimler ve onlar için yazılmış onaylı testler. Model
   üslubu (cümle kalıpları, ön koşul düzeni, madde işaretleri) buradan öğrenir. Eşleşme test
   dokümanındaki bağlantı sütunundan (ör. *SIT Link(s)*) kurulur.
2. **İzlenebilirlik bağlantılı doküman nesneleri**: Gereksinimin DOORS bağlantılarıyla ulaşılan SSDD/MKSD/...
   nesneleri. İki yön de izlenir: gereksinimin *Links* sütunu ve MKSD'nin *All Out Links* sütunu.
   Ekran/tuş/etiket/renk gibi kesin adlar buradan gelir.
3. **Benzerlikle bulunan doküman parçaları**: Bağlantı olmasa da ilgili bölümler (BM25, varsa embedding).
4. **Komşu gereksinimler** ve **hedef gereksinim**: metin, bölüm yolu, Rationale, Verification Method,
   Configuration. Bağlantı sütunları sızıntı olmasın diye gösterilmez.

Model JSON döndürür. Bozuk cevap bir kez onarılır. Sonuçlar `runs/<klasör>/items/` altına tek tek kaydedilir,
yani yarıda kalırsa aynı komut kaldığı yerden devam eder.

## Kurulum

Python 3.9+ yeterli; **paket kurulumu yok** (yalnızca standart kütüphane). İç ağa klasörü kopyalamak yeterli.

```text
copy config.example.json config.json
copy .env.example .env        (LLM_BASE_URL, LLM_API_KEY, LLM_CHAT_MODEL doldurun)
copy projects.example.json projects.json   (yolları ve rolleri düzenleyin)
python -m unittest discover -s tests
python -m reqtestgen check
```

Sunucu OpenAI uyumlu olmalı (`/v1/models`, `/v1/chat/completions`; embedding varsa `/v1/embeddings`).
Embedding modeli yoksa `LLM_EMBED_MODEL` boş kalır ve arama kelime tabanlı (BM25) yapılır.

## Komutlar

Menünün yaptığı her şey komutla da yapılabilir:

| Komut | Ne yapar |
|---|---|
| `python -m reqtestgen scan --data data` | data klasörünü tarar, `projects.json`'u yazar |
| `python -m reqtestgen pack` | iç ağa taşıma zip'i |
| `python -m reqtestgen inspect <dosya.htm> ...` | Hangi sütunun ne olarak okunduğunu, nesne türlerini ve örnek gereksinim/test case'leri gösterir |
| `python -m reqtestgen library --projects projects.json` | Proje başına: gereksinim, test case, eşleşen gereksinim, seçilen test bağlantı sütunu, dokümanlara bağlantı oranı |
| `python -m reqtestgen prompt --projects projects.json --target P --id ID` | Modele gidecek prompt'u gösterir (LLM çağırmaz); altında gerçek test (modele gitmez) |
| `python -m reqtestgen generate --projects projects.json --target P [--limit N] [--only ID,ID]` | Üretir; `tests.html` (+ gerçek test varsa `evaluation.html`) |
| `python -m reqtestgen crossval --projects projects.json [--limit N] [--judge]` | Her projeyi yalnızca diğerlerinin örnekleriyle üretir ve kendi testleriyle karşılaştırır |
| `python -m reqtestgen evaluate --projects projects.json --target P --run runs/...` | Var olan bir çalıştırmayı yeniden değerlendirir |

Deney seçenekleri: `--no-examples`, `--no-docs`, `--no-signals`, `--docs <ek yol>`, `--force`.

## projects.json

```json
{
  "docs": [{"path": "data/ORTAK/CC_SSS.htm", "role": "Ortak SSS"}],
  "projects": [
    {"name": "GMH_FLIR",
     "requirements": "data/GMH/CC_SSS_FLIR_SSS.htm",
     "tests": "data/GMH/aselflir-testdoc.htm",
     "docs": [{"path": "data/GMH/CC_MKSD_CC_MKSD.htm", "role": "MKSD - ekran formatları"},
              {"path": "data/GMH/CC_SSDD_CC_SSDD.htm", "role": "SSDD - tasarım"}],
     "doors": {"requirement_filter": {"Verification Method": ["Test"]}}},
    {"name": "YENI", "requirements": "data/YENI/srs.htm", "docs": ["data/YENI/dokumanlar/"]}
  ]
}
```

- `tests` olmayan proje yeni projedir: yalnızca üretilir, karşılaştırılmaz.
- `docs`: dosya ya da klasör. `role` her parçanın başında modele gösterilir. Programlar arasında isimler
  farklı olduğu için (GMH: SSS/MKSD/SSDD, Hürkuş-2: GMFD/MKB ...) rolü burada yazın.
- Proje bazında `html` / `doors` ayarları genel ayarların üstüne yazılır.
- Test dokümanlarını `docs` listesine koymayın. Konulsa da araç test modüllerini reddeder.

## Farklı export düzenleri

Sütunlar önce `html.columns` ile verilen adlardan, sonra takma adlardan, en son içerikten tanınır:

- **Tür sütunu**: "Object Type", "Quality" ya da değerleri çoğunlukla Heading/Requirement/Test Step olan sütun.
- **Ana sütun**: başlığı modül adı olan sütun ("FLIR SSS"). Eşlenmemiş, en çok metin içeren sütun seçilir.
- **Bağlantı**: başlığında *link / verifies / trace* geçen sütunlar. Test dokümanında, gereksinimlere en çok
  çözülen sütun otomatik seçilir (`library` çıktısında `test_link_column`).
- Hücre içi `<br>`/liste satır sonu olur. Madde görseli "•", iç tablo "a | b | c" satırı, şekil
  "[image: ...]" olarak okunur.

Yanlış okunan bir şey varsa `inspect` çıktısına bakıp `html.columns` ile düzeltin, örneğin
`{"main": "FLIR SSS", "object_type": "Quality", "frs_links": "SIT Link(s)"}`.

## Ölçütler (evaluation.html)

| Ölçüt | Anlamı |
|---|---|
| Kapsama | Test üretilen gereksinim oranı |
| Terim recall / precision | Gerçek testlerdeki adların (SMFD-3, LSK1, DCLT1, "HDG REF") üretilende geçme oranı. HMI testleri için ana otomatik ölçüt |
| Sinyal / kontrol / uyarım recall | Sinyal tarzı testler için (`SIG : (2) NORMAL`); HMI testlerinde boş kalır |
| Test case eşleşme | Her gerçek test case'in en benzer üretilen karşılığı eşiğin üstünde mi (TF-IDF ya da embedding) |
| Hakem (`--judge`) | Bir LLM'in davranış karşılaştırması: covered / partial / missing |

Otomatik ölçütler kabaca yön gösterir. Rapor için küçük bir alt kümede test mühendisi değerlendirmesi önerilir.

## Sınırlar

- Şekiller DOORS'ta OLE görseli; metinleri okunamaz (yalnızca Figure Caption).
- Bağlantısı olmayan gereksinimlerde bilgi yalnızca benzerlik aramasına kalır.
- Çıktı bir **taslak**tır; test mühendisi incelemesi gerekir. Açık sorular (`open_issues`) belgede listelenir.
