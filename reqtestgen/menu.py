"""Numbered menu for people who do not want to type commands (baslat.bat opens it).

Everything runs from the tool's folder: settings in .env / config.json, documents in data/,
projects.json written by the scan, results in runs/.
"""
from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
import shutil
import sys
import webbrowser
import zipfile
from pathlib import Path

from .config import load_config, read_env_file

ROOT = Path(__file__).resolve().parent.parent
PACK_EXCLUDE_DIRS = {"runs", "cache", "data", "__pycache__", ".git", ".venv"}
PACK_EXCLUDE_FILES = {".env", "config.json", "projects.json", "test_out.txt"}


# ---------------------------------------------------------------- helpers
def _ask(text: str, default: str = "") -> str:
    try:
        v = input(f"{text}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return v or default


def _pause() -> None:
    _ask("\nDevam etmek için Enter")


def _cli(*argv: str) -> int:
    from .cli import main

    print(f"\n> reqtestgen {' '.join(argv)}\n")
    try:
        return main(list(argv))
    except SystemExit as e:  # argparse / SystemExit("...") messages
        if e.code not in (None, 0):
            print(e.code if isinstance(e.code, str) else f"Çıkış kodu {e.code}")
        return 1 if e.code else 0


def _open(path: Path) -> None:
    print(f"Açılıyor: {path}")
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  (Windows)
    except AttributeError:
        webbrowser.open(path.resolve().as_uri())


def write_env(path: Path, values: dict) -> None:
    lines, done = [], set()
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            key = raw.split("=", 1)[0].strip()
            if "=" in raw and not raw.lstrip().startswith("#") and key in values:
                lines.append(f"{key}={values[key]}")
                done.add(key)
            else:
                lines.append(raw)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _projects() -> list[dict]:
    p = ROOT / "projects.json"
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8-sig")).get("projects", [])
    except (ValueError, OSError):
        return []


def _choose_project(only_new=False, only_tested=False) -> str | None:
    ps = [p for p in _projects() if (not only_new or not p.get("tests")) and (not only_tested or p.get("tests"))]
    if not ps:
        print("projects.json'da uygun proje yok. Önce 2 (dokümanları tara) çalıştırın.")
        return None
    for i, p in enumerate(ps, 1):
        print(f"  {i}) {p['name']}  ({'testli' if p.get('tests') else 'yeni proje'}; {Path(p['requirements']).name})")
    v = _ask("Proje numarası", "1")
    try:
        return ps[int(v) - 1]["name"]
    except (ValueError, IndexError):
        print("Geçersiz seçim.")
        return None


def _limit(default: str) -> list[str]:
    v = _ask("Kaç gereksinim? (0 = hepsi)", default)
    return [] if v in ("0", "") else ["--limit", v]


def _latest_report() -> Path | None:
    runs = ROOT / "runs"
    if not runs.is_dir():
        return None
    reports = [f for name in ("crossval.html", "evaluation.html", "tests.html") for f in runs.rglob(name)]
    return max(reports, key=lambda f: f.stat().st_mtime) if reports else None


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M")


# ---------------------------------------------------------------- actions
def setup() -> None:
    env_path = ROOT / ".env"
    if not (ROOT / "config.json").is_file() and (ROOT / "config.example.json").is_file():
        shutil.copy(ROOT / "config.example.json", ROOT / "config.json")
    env = read_env_file(env_path)
    print("LLM sunucusu: OpenAI uyumlu adres, sürüm yolu dahil (ör. http://llm.sirket.local/v1)")
    base = _ask("Adres", env.get("LLM_BASE_URL", ""))
    if not base.lower().startswith(("http://", "https://")):
        print("Adres http:// ya da https:// ile başlamalı.")
        return
    key = getpass.getpass("API anahtarı (yazdıkça görünmez; Enter = değiştirme / anahtar yok): ").strip()
    values = {"LLM_BASE_URL": base.rstrip("/"), "LLM_API_KEY": key or env.get("LLM_API_KEY", "")}
    write_env(env_path, values)

    from .llm import LLMClient, LLMError

    cfg = load_config(str(ROOT / "config.json") if (ROOT / "config.json").is_file() else None, str(env_path))
    try:
        models = LLMClient(cfg["llm"]).list_models()
    except LLMError as e:
        print(f"Sunucuya bağlanılamadı: {e}")
        return
    if not models:
        print("Sunucu model listesi döndürmedi. Model adını elle yazın.")
        chat = _ask("Sohbet modeli adı", env.get("LLM_CHAT_MODEL", ""))
        embed = _ask("Embedding modeli adı (yoksa boş)", env.get("LLM_EMBED_MODEL", ""))
    else:
        print("Sunucudaki modeller:")
        for i, m in enumerate(models, 1):
            print(f"  {i}) {m}")

        def pick(text, current, allow_none):
            d = str(models.index(current) + 1) if current in models else ("0" if allow_none else "1")
            v = _ask(text + (" (0 = yok)" if allow_none else ""), d)
            if allow_none and v == "0":
                return ""
            try:
                return models[int(v) - 1]
            except (ValueError, IndexError):
                return v  # a name typed by hand

        chat = pick("Test yazacak sohbet modeli", env.get("LLM_CHAT_MODEL", ""), False)
        embed = pick("Embedding modeli (adında 'embed' geçen; yoksa 0)", env.get("LLM_EMBED_MODEL", ""), True)
    values.update({"LLM_CHAT_MODEL": chat, "LLM_EMBED_MODEL": embed})
    write_env(env_path, values)
    print(".env kaydedildi. Bağlantı deneniyor...")
    _cli("check")


def scan_documents() -> None:
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    progs = [d for d in data.iterdir() if d.is_dir()]
    if not progs:
        print(f"{data} boş. Her program için bir klasör açıp DOORS HTML export'larını içine koyun:\n"
              f"  {data}\\GMH\\...htm   {data}\\HURKUS2\\...htm   (ortak dokümanlar: {data}\\ORTAK\\)")
        _open(data)
        return
    target = ROOT / "projects.json"
    if target.is_file():
        backup = ROOT / f"projects_{_stamp()}.bak.json"
        shutil.copy(target, backup)
        print(f"Önceki projects.json yedeklendi: {backup.name}")
    if _cli("scan", "--data", str(data), "--out", str(target)) != 0:
        return
    content = json.loads(target.read_text(encoding="utf-8"))
    changed = False
    for p in content["projects"]:
        guess = p.get("requirements_guess")
        if not guess or p.get("tests"):
            continue
        print(f"\n{p['name']} yeni proje (testi yok). Gereksinim modülü hangisi?")
        for i, g in enumerate(guess, 1):
            print(f"  {i}) {g}")
        v = _ask("Numara", "1")
        try:
            chosen = guess[int(v) - 1]
        except (ValueError, IndexError):
            continue
        if chosen != p["requirements"]:
            p["docs"] = [d for d in p["docs"] if d["path"] != chosen] + [{"path": p["requirements"], "role": "doküman"}]
            p["requirements"] = chosen
        p.pop("requirements_guess", None)
        changed = True
    if changed:
        target.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    _cli("library", "--projects", str(target))


def crossval() -> None:
    if not any(p.get("tests") for p in _projects()):
        print("Testi olan proje yok; karşılaştırma yapılamaz.")
        return
    args = ["crossval", "--projects", str(ROOT / "projects.json"), "--out", str(ROOT / "runs" / f"dogrulama_{_stamp()}")]
    args += _limit("10")
    if _ask("Hakem model de değerlendirsin mi? (e/h)", "h").lower().startswith("e"):
        args.append("--judge")
    if _cli(*args) == 0:
        _open(Path(args[4]) / "crossval.html")


def generate() -> None:
    name = _choose_project()
    if not name:
        return
    out = ROOT / "runs" / f"{name}_{_stamp()}"
    args = ["generate", "--projects", str(ROOT / "projects.json"), "--target", name, "--out", str(out)] + _limit("0")
    if _cli(*args) == 0:
        _open(out / "tests.html")


def show_prompt() -> None:
    name = _choose_project()
    if not name:
        return
    rid = _ask("Gereksinim ID'si")
    if not rid:
        return
    out = ROOT / "runs" / f"prompt_{rid.replace(' ', '_')}.txt"
    if _cli("prompt", "--projects", str(ROOT / "projects.json"), "--target", name, "--id", rid,
            "--out", str(out)) == 0:
        _open(out)


def automatic() -> None:
    """Scan, check, short validation on tested projects, generation for new projects."""
    scan_documents()
    ps = _projects()
    if not ps:
        return
    if any(p.get("tests") for p in ps):
        out = ROOT / "runs" / f"dogrulama_{_stamp()}"
        if _cli("crossval", "--projects", str(ROOT / "projects.json"), "--limit", "10", "--out", str(out)) == 0:
            _open(out / "crossval.html")
    for p in ps:
        if not p.get("tests"):
            out = ROOT / "runs" / f"{p['name']}_{_stamp()}"
            if _cli("generate", "--projects", str(ROOT / "projects.json"), "--target", p["name"], "--out", str(out)) == 0:
                _open(out / "tests.html")


def pack(out: Path | None = None) -> Path:
    out = out or ROOT.parent / f"ReqTestGen_{_stamp()}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(ROOT.rglob("*")):
            rel = f.relative_to(ROOT)
            if f.is_dir() or any(part in PACK_EXCLUDE_DIRS for part in rel.parts[:-1]) \
                    or rel.parts[0] in PACK_EXCLUDE_DIRS or rel.name in PACK_EXCLUDE_FILES \
                    or f.suffix in (".pyc", ".zip") or rel.name.endswith(".bak.json"):
                continue
            z.write(f, Path("ReqTestGen") / rel)
    return out


# ------------------------------------------------------------------- menu
def _status() -> str:
    env = read_env_file(ROOT / ".env")
    llm = env.get("LLM_BASE_URL") or "(ayarlanmadı)"
    model = env.get("LLM_CHAT_MODEL") or "(seçilmedi)"
    data = ROOT / "data"
    progs = [d.name for d in data.iterdir() if d.is_dir()] if data.is_dir() else []
    ps = _projects()
    last = _latest_report()
    return (f"  LLM: {llm}  model: {model}\n"
            f"  data klasörü: {', '.join(progs) or '(boş)'}\n"
            f"  projects.json: {len(ps)} proje ({sum(1 for p in ps if p.get('tests'))} testli)\n"
            f"  son rapor: {last.relative_to(ROOT) if last else '-'}")


ACTIONS = [
    ("1", "Kurulum: LLM adresi, anahtar, model seçimi ve bağlantı testi", setup),
    ("2", "Dokümanları tara (data klasörü) ve projeleri otomatik oluştur", scan_documents),
    ("3", "Doğrulama: testli projelerde üret ve gerçek testlerle karşılaştır", crossval),
    ("4", "Test üret (proje seç)", generate),
    ("5", "Bir gereksinim için modele gidecek metni göster (LLM çağırmaz)", show_prompt),
    ("6", "Son raporu aç", lambda: _open(_latest_report()) if _latest_report() else print("Henüz rapor yok.")),
    ("7", "Tam otomatik: tara + doğrula (10 gereksinim) + yeni projeler için üret", automatic),
    ("8", "Taşıma paketi (zip) oluştur: kod + taşınabilir Python, veri ve anahtar hariç",
     lambda: print(f"Paket oluşturuldu: {pack()}")),
]


def run_menu() -> int:
    os.chdir(ROOT)
    (ROOT / "data").mkdir(exist_ok=True)
    while True:
        print("\n" + "=" * 70 + "\n ReqTestGen - DOORS gereksinimlerinden test taslağı\n" + "=" * 70)
        print(_status() + "\n")
        for key, label, _ in ACTIONS:
            print(f"  {key}) {label}")
        print("  0) Çıkış")
        choice = _ask("\nSeçim")
        if choice == "0":
            return 0
        action = next((a for k, _, a in ACTIONS if k == choice), None)
        if action is None:
            continue
        try:
            action()
        except KeyboardInterrupt:
            print("\nDurduruldu. Aynı işlemi tekrar başlatınca kaldığı yerden devam eder.")
        except Exception as e:  # keep the menu alive; show the problem
            print(f"\nHata: {type(e).__name__}: {e}")
        _pause()


if __name__ == "__main__":
    sys.exit(run_menu())
