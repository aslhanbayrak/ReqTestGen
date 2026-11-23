# AGENTS.md: ReqTestGen üzerinde çalışan yapay zeka için

## Ne yapar
DOORS HTML export'larından gereksinimleri okur. Her gereksinim için LLM'e şunlardan oluşan bir prompt gönderir:
diğer projelerin onaylı test örnekleri, izlenebilirlik bağlantılarıyla bulunan tasarım/ekran nesneleri,
benzerlikle bulunan doküman parçaları ve hedef gereksinim. Dönen JSON test case'lerini HTML test dokümanına
yazar. Gerçek testi olan projelerde üretilen testleri gerçeklerle karşılaştırır. Model eğitimi yoktur
(few-shot + RAG).

## Modüller (`reqtestgen/`)
| Dosya | Görev |
|---|---|
| `config.py` | DEFAULTS + config.json + .env (LLM_*). API anahtarı yalnızca .env/ortam değişkeninden |
| `html_import.py` | HTML -> DoorsModule. Tablo seçimi, sütun eşleme (ad, takma ad, içerik), hücre metni (br, madde görseli, iç tablo), `inspect_html` |
| `doors.py` | DoorsObject/Module, hiyerarşi, ID çözümleme (`IdIndex`, `extract_ids`), sınıflandırma, `extract_requirements` (filtre, bağlantılar), `extract_test_cases` (+ `subset_for`) |
| `library.py` | projects.json, `load_project` (test bağlantı sütunu seçimi, iki yönlü bağlantı), `ExampleLibrary` (örnek arama) |
| `knowledge.py` | Sistem dokümanları: DOORS tabloları -> ID'li nesneler + bağlantı grafiği (`linked`), diğerleri -> başlık bazlı parçalar; test modüllerini reddeder |
| `search.py` | BM25, embedding (opsiyonel), hibrit RRF, embedding önbelleği |
| `signals.py` | Sinyal sözlüğü (model XML, liste, örneklerden). HMI tarzı testlerde boş kalır, sorun değil |
| `prompts.py` | Tüm prompt metinleri, ayar için ilk bakılacak yer |
| `generate.py` | Prompt kurma (`build_messages`), JSON onarımı, paralel üretim, kaldığı yerden devam |
| `evaluate.py` | Kapsama, sinyal/kontrol recall, terim recall/precision, test case benzerliği, hakem model |
| `report.py` | tests.html, evaluation.html, crossval.html |
| `cli.py` | Komutlar: check, inspect, library, prompt, generate, evaluate, crossval, scan, pack, menu |
| `discover.py` | `data/<program>/` klasörlerinden projects.json: test dosyası, bağlantılarla gereksinim modülü, kalanlar doküman; `group` = program |
| `menu.py` | Numaralı menü (`baslat.bat` -> `baslat.py`): kurulum sihirbazı (.env, model seçimi), tara, doğrula, üret, paketle |
| `llm.py` | OpenAI uyumlu istemci (urllib), yeniden deneme, ölümcül hata ayrımı, reasoning/fence temizliği |

## Değişmez kurallar
1. **Sızıntı yok.** Hedef projenin testleri prompt'a girmez:
   - örnekler `exclude_projects` ile yalnızca diğer projelerden gelir;
   - `knowledge.py` test modüllerini (Object Type içinde "test") doküman olarak kabul etmez;
   - `generate.prompt_attributes` bağlantı sütunlarını hiç yazmaz;
   - testli (değerlendirilen) bir projeye kendi `group`'undan (programından) örnek verilmez (`ProjectsFile.example_projects`).
   Yeni bir bağlam kaynağı eklersen aynı garantiyi sağla ve test yaz.
2. Yalnızca standart kütüphane, Python 3.9+. (`from __future__ import annotations` kullanılıyor.)
3. Program/modül/sütun adı koda yazılmaz. Farklar `config.json` ve `projects.json` (proje bazında
   `html`, `doors` override; `docs[].role`) ile verilir.
4. Girdi HTML, çıktı HTML. JSON yalnızca ara kayıt (`items/*.json`, `results.json`, `evaluation.json`).
5. Ölümcül LLM hataları (401/403, bağlantı yok) toplu işi durdurur. Geçici hatalar yeniden denenir.
6. Kullanıcıya dönük mesajlar Türkçe, kod/yorumlar İngilizce.

## Test
```text
python -m unittest discover -s tests
```
- `tests/fake_llm.py`: sahte OpenAI uyumlu sunucu (ağ gerekmez).
- `examples/gmh_demo/`: gerçek GMH export düzenini taklit eden **sentetik** örnek (Quality sütunu, modül adlı
  ana sütun, SIT Link(s) + Links, adım bazlı bağlantı, madde görseli, hücre içi tablo, OLE şekil,
  MKSD/SSDD bağlantıları). Gerçek veriyi teste koyma; yeni bir yapı görürsen buraya sentetik karşılığını ekle.
- `examples/proje_a|b|c`: IFF/simülatör tarzı eski örnekler.

## Hata ayıklama sırası
1. `inspect <dosya>`: sütun eşleme ve nesne türleri doğru mu?
2. `library --projects ...`: test bağlantı sütunu, eşleşen gereksinim sayısı, dokümanlara bağlantı oranı.
3. `prompt --id <ID>`: modelin gördüğü metin. Bilgi eksik mi, sızıntı var mı?
4. `generate --limit 5` / `crossval --limit 10`: küçük ölçüm, sonra büyüt.
