# İç ağdaki yapay zekaya verilecek ilk prompt

Bu dosya iki bölümden oluşuyor:

1. **Kopyala-yapıştır prompt**: Proje iç ağa alındıktan sonra iç ağdaki yapay zekaya (DeepSeek / Qwen vb.,
   kod yazabilen ve dosya okuyabilen bir asistan) ilk mesaj olarak verilecek metin.
2. **Senin için notlar**: Prompt'u vermeden önce hazırlaman gerekenler ve süreç boyunca nelere dikkat edeceğin.

Yapay zekanın okuyacağı diğer dosyalar: `AGENTS.md` (kod mimarisi ve kurallar), `docs/GMH_DOKUMAN_ANALIZI.md`
(dış ağda fotoğraflardan çıkarılan doküman yapısı), `README.md` (kullanım).

---

## 1. Kopyala-yapıştır prompt

> Aşağıdaki metnin tamamını ilk mesaj olarak ver. `<...>` ile işaretli yerleri kendi yollarınla doldur.

```text
Sen bu projede benim kıdemli yazılım + doğrulama (verification) mühendisi yardımcımsın. Proje: ReqTestGen.
DOORS'tan HTML olarak dışa aktarılmış gereksinimlerden, kurumun mevcut test dokümanlarının üslubunda test
prosedürü taslağı üreten, tamamen standart kütüphaneyle yazılmış bir Python aracı. Kod dış ağda yazıldı;
gerçek dokümanları hiç görmedi, yalnızca birkaç ekran fotoğrafından yola çıkıldı. Senin işin aracı gerçek
doküman setine uyarlamak, ölçmek, iyileştirmek ve bir araştırma raporu çıkarmak.

ÖNCE OKU (sırayla): README.md, AGENTS.md, docs/GMH_DOKUMAN_ANALIZI.md, IC_AG_ILK_PROMPT.md (bu dosya).
Okuduktan sonra bana 10 maddeyi geçmeyen bir özet ver: aracın akışı, değişmez kurallar, ilk adımda ne
yapacağın. Sonra aşağıdaki aşamaları SIRAYLA yürüt. Her aşamanın sonunda bana kısa bir rapor ver ve
onayımı bekle; bir aşamayı atlama.

DOKÜMAN SETİ
- Programlar ve klasörler:
  - GMH: <yol>  (test dokümanı: SIT test doc; gereksinim: SSS; diğerleri: MKSD, SSDD, ...)
  - Hürkuş-2: <yol>  (gereksinim/tasarım isimleri farklı: GMFD, MKB, ...)
  - <diğer programlar>: <yol>
- Hepsi DOORS HTML export. Her program farklı modül adları, farklı sütunlar, farklı hiyerarşi kullanıyor.
  Hiçbir proje adını, modül adını, sütun adını koda sabit yazma; farklar projects.json ve config.json ile
  verilir. Kod değişikliği ancak yapılandırmayla çözülemeyen bir durumda yapılır.

AŞAMA 0 - Ortam
- Kullanıcı aracı `baslat.bat` menüsüyle kullanıyor (menu.py); sen komutları doğrudan çalıştırabilirsin.
  Menü akışını bozma: yeni bir yetenek eklersen menüye de bir seçenek olarak ekle.
- python --version (>= 3.9; klasörde python\python.exe varsa o). `python -m unittest discover -s tests`
  çalıştır, hepsinin geçtiğini göster.
- LLM ayarı menüde "1 Kurulum" ile yapıldı mı kontrol et (.env: LLM_BASE_URL, LLM_CHAT_MODEL, varsa
  LLM_EMBED_MODEL). `python -m reqtestgen check` çıktısını göster. Embedding modeli yoksa boş kalır (BM25).
- Dokümanlar data\<program>\ klasörlerinde. `python -m reqtestgen scan --data data` ile projects.json
  otomatik oluşur; AŞAMA 3'te bunu kanıta göre düzelteceksin.

AŞAMA 1 - Doküman envanteri (henüz kod değiştirme)
- Her program klasöründeki HER HTML dosyası için `python -m reqtestgen inspect <dosya> --show 5` çalıştır.
- Bir tablo çıkar: dosya | modül adı (ana sütun başlığı) | tür (test / gereksinim / tasarım / arayüz /
  ekran formatı / test ortamı / plan / diğer) | ID öneki (FLIR-SSS-, CC-SSDD- ...) | nesne türü sütunu
  ("Object Type" / "Quality" ...) | bağlantı sütunları ve dolu hücre sayıları | Object Type dağılımı.
- Okuma hatalarını listele: ana sütun yanlış seçildiyse, tür sütunu bulunamadıysa, başlıklar gereksinim
  olarak okunduysa, hücre içi tablo/madde işareti bozulduysa. Bunları önce config/projects.json ile
  (html.columns, doors.*) çöz; çözülmüyorsa küçük, testli bir kod değişikliği öner.

AŞAMA 2 - İzlenebilirlik grafiği: testçiler gerçekte hangi dokümanlara bakıyor?
- Bana "testi yazarken SSS, MKSD, SSDD'ye bakılıyor" denildi ama daha fazlasına bakıldığını düşünüyorum.
  Bunu kanıta dayalı bul:
  a) Test dokümanındaki bağlantı sütunlarının (SIT Link(s), Links ...) hangi modüllere gittiğini say
     (ID öneklerine göre).
  b) Gereksinim modülündeki bağlantı sütunlarının (Links, Out-links ..., SPS Link ID) hangi modüllere
     gittiğini say. MKSD/SSDD'deki "All Out Links" gibi geri bağlantıları da say.
  c) 20 rastgele test adımını al (farklı test case'lerden). Her adımdaki her özel adı (ekran: SMFD-2,
     VMFD-1, KDU-2; tuş: LSK1, soft key etiketi DCLT1; sayfa: FLIR page; renk; mod; konfigürasyon:
     "TUHP Simulator, Change Configuration : 1.with EGI") tüm doküman setinde ara (grep). Hangi dokümanda
     ilk ve en ayrıntılı geçtiğini yaz. Bağlı gereksinimde geçmeyen bilgiler testçinin başka bir dokümana
     baktığını gösterir.
  d) Sonuç: doküman türü -> test yazımındaki rolü -> kanıt (örnek adım + bulunduğu yer) tablosu.
     Muhtemel adaylar: SSS, MKSD (ekran/tuş formatları), SSDD (tasarım), ICD/IDD (arayüz),
     SPS (üst sistem spesifikasyonu), test ortamı / simülatör (TUHP) konfigürasyon dokümanı,
     STP/SITP (test planı: konfigürasyonlar, ortak ön koşullar), konfigürasyon tanım dokümanı
     (Common / DGF / "Common w FLIR"), pilot el kitabı benzeri operasyon dokümanları.
  e) Hürkuş-2 için aynı analizi yap (GMFD, MKB ve diğerleri). İsim farklarını eşleştir: hangi Hürkuş-2
     dokümanı GMH'deki hangi dokümanın rolünü oynuyor.

AŞAMA 3 - projects.json
- scan çıktısını başlangıç al; eşleşmeleri ve "group" (program) alanlarını kontrol et.
- Her program için bir proje girdisi olsun: requirements (gereksinim modülü), tests (test dokümanı),
  docs (AŞAMA 2'de rolü kanıtlanan dokümanlar, her birine "role" ver: ör. "MKSD - MFD/KDU ekran
  formatları", "GMFD - ..."). Bir programda birden fazla gereksinim/test modülü varsa her çift ayrı proje
  olabilir (ör. GMH_FLIR, GMH_EGI).
- Gerekirse doors.requirement_filter (ör. {"Verification Method": ["Test"]}), html.columns, part/step
  türleri. Bunları hangi kanıta dayanarak koyduğunu yaz.
- `python -m reqtestgen library --projects projects.json` çıktısını göster. Kontrol et:
  test_link_column doğru mu; requirements_with_tests makul mü; unmatched_links neden var;
  "bağlantısı olan gereksinim" oranı ne (dokümanlara izlenebilirlik).

AŞAMA 4 - Prompt'u gözle doğrula (LLM çağrısı yok)
- 5 farklı türde gereksinim seç (ekran formatı, tuş/mod geçişi, sayısal sınır, arıza/robustness,
  konfigürasyona bağlı). Her biri için `python -m reqtestgen prompt --projects projects.json --target <P>
  --id <ID> --out runs/prompt_<ID>.txt`.
- Her prompt için: gerçek testi yazmak için gereken bilgi prompt'ta var mı? Yoksa hangi dokümanda?
  Neden gelmedi (bağlantı yok, benzerlik bulamadı, bütçe doldu, doküman eklenmemiş)? Gerçek testi sızdıran
  bir şey var mı (test metni, test ID'si, formal run sonucu)? Sızıntı varsa bu en yüksek önceliklidir.

AŞAMA 5 - Ölçüm (çapraz deneme)
- Önce küçük: `python -m reqtestgen crossval --projects projects.json --limit 10`. Sonuçları ve
  evaluation.html'deki yan yana karşılaştırmaları incele.
- Sonra tam çalıştırma. Ablasyonlar (her biri ayrı klasöre): örneksiz (--no-examples), dokümansız
  (--no-docs), bağlantı izlemesiz (config: knowledge.follow_links=false), link_depth 2, k değerleri.
- Ölçütler: kapsama, terim recall/precision (HMI testleri için ana otomatik ölçüt), test case eşleşme,
  hakem (--judge; hakem modeli üreten modelden farklı olursa daha güvenilir). 20 gereksinimlik bir alt
  kümede bir test mühendisiyle insan değerlendirmesi planla (doğru / küçük düzeltme / yanlış).

AŞAMA 6 - İyileştirme döngüsü
- Hataları sınıflandır: (1) okuma/eşleme hatası, (2) eksik bilgi (doküman yok/bağlantı yok),
  (3) üslup (örneklerin kalıbına uymama), (4) uydurma ad/değer, (5) gereksiz/yanlış test.
- Her sınıf için en küçük değişikliği yap (önce yapılandırma, sonra prompts.py, en son kod), testi ekle,
  aynı alt kümede tekrar ölç. Değişiklik + önce/sonra sayıları bir değişiklik günlüğüne yazılsın.

AŞAMA 7 - Araştırma raporu (Türkçe)
- Bölümler: problem ve hedef; doküman seti ve izlenebilirlik analizi (AŞAMA 2 tablosu); yöntem (RAG:
  örnek test erişimi + bağlantı izleme + benzerlik araması, few-shot, model eğitimi yok); deney düzeni
  (leave-one-project-out, sızıntı önlemleri); sonuçlar ve ablasyonlar; hata analizi örneklerle; sınırlar
  (şekiller OLE görsel olduğu için okunamıyor, bağlantısız gereksinimler, model boyutu); öneriler ve
  sonraki adımlar (ör. görsel model, test mühendisi geri bildirimi, DOORS'a geri aktarım).

DEĞİŞMEZ KURALLAR
1. Sızıntı yok: değerlendirilen projenin testleri hiçbir şekilde prompt'a girmez. Örnekler yalnızca diğer
   projelerden gelir (kod bunu sağlıyor; bozma). Test dokümanlarını docs listesine koyma (kod test
   modüllerini zaten reddediyor). Gereksinim tarafındaki bağlantı sütunları prompt'a yazılmaz.
2. Yalnızca Python standart kütüphanesi. pip paketi ekleme.
3. Girdi DOORS HTML, çıktı HTML (tests.html, evaluation.html, crossval.html). JSON yalnızca ara/kayıt.
4. Proje/modül/sütun adı koda yazılmaz; yapılandırmaya yazılır.
5. Her kod değişikliğinden sonra `python -m unittest discover -s tests`. Yeni davranış = yeni test
   (gerçek veriyi teste koyma; examples/ altındaki sentetik örnekleri genişlet).
6. Gerçek doküman içeriği iç ağdan çıkmaz; harici servis kullanılmaz; sertifika doğrulaması kapatılmaz.
7. Emin olmadığın bir şeyi uydurma: "bilmiyorum, şunu kontrol etmem gerekiyor" de ve nasıl kontrol
   edeceğini söyle.

Başla: önce dosyaları oku ve özetini ver.
```

---

## 2. Senin için notlar

### Prompt'u vermeden önce
- Dış ağda `baslat.bat` > 8 ile zip oluştur, iç ağda aç. Python yoksa embeddable Python'u `python\` klasörüne aç.
- `baslat.bat` > 1 ile LLM ayarını yap, > 2 ile dokümanları tarat. Yapay zekaya prompt'u bundan sonra ver.
- Doküman setini program bazında `data\` altındaki klasörlere koy. Örnek düzen:

```text
data/
  GMH/        aselflir-testdoc.htm, CC_SSS_FLIR_SSS.htm, CC_MKSD_CC_MKSD.htm, CC_SSDD_CC_SSDD.htm, ...
  HURKUS2/    <test dokümanı>.htm, <GMFD>.htm, <MKB>.htm, ...
```

- DOORS'tan HTML alırken: ilgili görünümde (view) **Object Identifier, Object Type/Quality, ana sütun,
  Expected Results, tüm bağlantı sütunları** görünür olsun. Bağlantı sütunları (SIT Link(s), Links,
  Out-links, All Out Links) aracın dokümanlar arasında yol bulmasının temeli; mümkünse
  "linked object identifier + text" gösteren layout DXL sütunu ekleyin.
- Formal run sütunları (CT.01.08 ...) zarar vermez, ama export'u şişirir; istersen görünümden çıkar.

### Süreç boyunca
- Her aşama sonunda yapay zekanın raporunu oku. Özellikle AŞAMA 2 (hangi dokümanlar gerçekten kullanılıyor)
  ve AŞAMA 4 (sızıntı) senin onayını gerektiren kararlardır.
- Bir test mühendisine 1 saatlik görüşme için şunu sor: "Bu adımı yazarken hangi dokümanı açtınız?"
  20 adım için cevapları AŞAMA 2'nin kanıtıyla karşılaştır; raporda çok değerli bir bölüm olur.
- Model küçükse (ör. 8B) `llm.concurrency` 1-2, `retrieval.max_example_chars` ve `knowledge.max_chars`
  daha düşük olsun; büyükse artır. Düşünen (reasoning) modellerde `llm.max_tokens` yüksek kalmalı.
- Sonuçlar `runs/` altında klasör klasör saklanır; rapordaki her sayının hangi klasörden geldiğini not et.
