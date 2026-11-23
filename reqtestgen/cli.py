"""Command line: python -m reqtestgen <command> ...  (python -m reqtestgen -h for help)"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .config import load_config
from .doors import _matches, extract_requirements, extract_test_cases, load_module, read_text_any
from .evaluate import evaluate
from .generate import GenerationResult, Generator, run_generation, select_requirements
from .html_import import inspect_html
from .knowledge import KnowledgeBase, load_documents
from .library import ExampleLibrary, ProjectSpec, load_project, load_projects
from .llm import LLMClient, LLMError
from .report import crossval_html, evaluation_html, test_document_html
from .search import EmbeddingCache, resolve_mode
from .signals import Vocabulary


def _stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- setup
class Context:
    """Everything one generation needs for one target project."""

    def __init__(self, args, cfg: dict, llm: LLMClient, cache: EmbeddingCache, target: ProjectSpec,
                 library_specs: list[ProjectSpec], shared_signals, shared_docs, doc_roles=None):
        self.cfg, self.llm, self.target = cfg, llm, target
        tcfg = target.config(cfg)
        print(f"Hedef proje: {target.name}")
        self.target_data = load_project(target, cfg)
        print(f"  {len(self.target_data.requirements)} gereksinim okundu ({target.requirements.name})")
        if target.tests:
            print(f"  Gerçek testler: {len(self.target_data.cases)} test case, "
                  f"{len(self.target_data.cases_by_req)} gereksinime bağlı "
                  f"(bağlantı sütunu: {self.target_data.link_column})")

        self.library = None
        lib_data = []
        if not getattr(args, "no_examples", False):
            for spec in library_specs:
                pd = load_project(spec, cfg)
                lib_data.append(pd)
                st = pd.stats()
                print(f"  Örnek proje {spec.name}: {st['requirements']} gereksinim, {st['test_cases']} test case, "
                      f"{st['requirements_with_tests']} gereksinime bağlı"
                      + (f", eşleşmeyen bağlantı: {st['unmatched_links']}" if st["unmatched_links"] else ""))
            self.library = ExampleLibrary(lib_data, cfg, llm, cache)
            print(f"  Örnek kütüphanesi: {len(self.library)} gereksinim-test çifti (arama: {resolve_mode(cfg)})")

        self.vocab = Vocabulary()
        if not getattr(args, "no_signals", False):
            self.vocab.load_paths(list(shared_signals) + list(target.signals) + [Path(p) for p in args.signals or []])
            for pd in lib_data:
                self.vocab.learn_from_cases(pd.cases)
            print(f"  Sinyal sözlüğü: {len(self.vocab)} sinyal, {len(self.vocab.simulators)} simülatör")

        self.kb = None
        if not getattr(args, "no_docs", False):
            paths = list(shared_docs) + list(target.docs) + [Path(p) for p in args.docs or []]
            if paths:
                chunks, objects = load_documents(paths, tcfg, doc_roles)
                self.kb = KnowledgeBase(chunks, tcfg, llm, cache, objects)
                reqs = self.target_data.requirements
                n_linked = sum(1 for r in reqs if self.kb.linked(r.id, r.links))
                print(f"  Sistem dokümanları: {len(self.kb)} bölüm, {len(objects)} ID'li DOORS nesnesi; "
                      f"dokümanlara izlenebilirlik bağlantısı olan gereksinim: {n_linked}/{len(reqs)}")

        context = []
        for p in (cfg["generation"].get("context_file"), target.context):
            if p and Path(p).is_file():
                context.append(read_text_any(Path(p)))
        self.generator = Generator(tcfg, llm, self.library, self.vocab, self.kb, "\n\n".join(context))

    def run(self, out: Path, only=None, limit=None, force=False, with_tests_only=False) -> list[GenerationResult]:
        reqs = self.target_data.requirements
        targets = reqs
        if with_tests_only:
            targets = [r for r in reqs if r.id in self.target_data.cases_by_req]
        targets = select_requirements(targets, only, limit)
        print(f"  {len(targets)} gereksinim için test üretilecek -> {out}")
        results = run_generation(self.generator, reqs, targets, out, exclude_projects=(self.target.name,), force=force)
        info = {"proje": self.target.name, "model": self.cfg["llm"].get("chat_model"),
                "örnek arama": resolve_mode(self.cfg) if self.library else "kapalı",
                "örnek çifti": len(self.library) if self.library else 0,
                "sinyal": len(self.vocab), "doküman bölümü": len(self.kb) if self.kb else 0,
                **{k.replace("_", " "): v for k, v in self.llm.usage().items()}}
        _write(out / "results.json", json.dumps({"info": info, "results": [asdict(r) for r in results]},
                                                ensure_ascii=False, indent=1))
        _write(out / "tests.html", test_document_html(f"{self.target.name} - üretilen testler", reqs, results, info))
        ok = sum(1 for r in results if r.status == "ok")
        print(f"  Bitti: {ok}/{len(results)} gereksinim, {sum(len(r.test_cases) for r in results)} test case. "
              f"Doküman: {out / 'tests.html'}")
        return results


def _llm(cfg: dict) -> LLMClient:
    return LLMClient(cfg["llm"])


def _cache(args) -> EmbeddingCache:
    return EmbeddingCache(Path(args.cache) / "embeddings.json")


def _resolve_target(args, cfg):
    """(target spec, example project specs, shared signals, shared docs, doc roles)"""
    if args.projects:
        pf = load_projects(args.projects)
        if args.target:
            target = pf.get(args.target)
        elif args.requirements:
            target = ProjectSpec(name=Path(args.requirements).stem, requirements=Path(args.requirements).resolve())
        else:
            raise SystemExit("--target <proje adı> ya da --requirements <dosya> verin")
        return target, pf.example_projects(target), pf.signals, pf.docs, pf.doc_roles
    if not args.requirements:
        raise SystemExit("--projects projects.json ya da --requirements <dosya> verin")
    return ProjectSpec(name=Path(args.requirements).stem, requirements=Path(args.requirements).resolve()), [], [], [], {}


def _load_results(run: Path) -> list[GenerationResult]:
    f = run / "results.json"
    if f.is_file():
        return [GenerationResult.from_json(d) for d in json.loads(f.read_text(encoding="utf-8"))["results"]]
    items = sorted((run / "items").glob("*.json"))
    if not items:
        raise SystemExit(f"{run} içinde sonuç yok (results.json ya da items/)")
    return [GenerationResult.from_json(json.loads(p.read_text(encoding="utf-8"))) for p in items]


def _evaluate_and_report(ctx_cfg, llm, cache, target_data, results, out: Path, judge: bool) -> dict:
    ev = evaluate(target_data.requirements, results, target_data.cases_by_req, ctx_cfg, llm, judge, cache)
    _write(out / "evaluation.json", json.dumps(ev, ensure_ascii=False, indent=1))
    _write(out / "evaluation.html", evaluation_html(f"{target_data.name} - değerlendirme", ev, results,
                                                    target_data.cases_by_req))
    s = ev["summary"]

    def pct(v):
        return "-" if v is None else f"{v * 100:.0f}%"

    print(f"  Değerlendirme ({s['evaluated_requirements']} gereksinim): kapsama {pct(s['coverage'])}, "
          f"sinyal recall {pct(s['signal_recall'])}, kontrol recall {pct(s['check_recall'])}, "
          f"terim recall {pct(s['term_recall'])}, "
          f"test case eşleşme {pct(s['case_match_recall'])}"
          + (f", hakem {pct(s['judge_recall'])}" if s.get("judge_recall") is not None else "")
          + f" -> {out / 'evaluation.html'}")
    return ev


# ------------------------------------------------------------- commands
def cmd_check(args, cfg) -> int:
    llm = _llm(cfg)
    print(f"Adres: {cfg['llm']['base_url'] or '(tanımlı değil)'}")
    print(f"Anahtar: {'var' if cfg['llm']['api_key'] else 'yok'}")
    models = llm.list_models()
    print(f"Sunucudaki modeller ({len(models)}):")
    for m in models:
        print(f"  - {m}")
    for role in ("chat_model", "embed_model", "judge_model"):
        name = cfg["llm"].get(role)
        if name:
            print(f"{role}: {name} {'(listede var)' if name in models else '(LİSTEDE YOK - adı kontrol edin)'}")
    if cfg["llm"].get("chat_model"):
        reply = llm.chat([{"role": "user", "content": 'Return {"ok": true} as JSON.'}], max_tokens=2000)
        print(f"Sohbet testi: {reply[:200]!r}")
    else:
        print("chat_model seçilmemiş: config.json > llm.chat_model")
    if cfg["llm"].get("embed_model"):
        v = llm.embed(["test"])[0]
        print(f"Embedding testi: {len(v)} boyutlu vektör")
    else:
        print("embed_model yok: örnek ve doküman araması kelime tabanlı (BM25) yapılacak")
    print(f"Kullanım: {llm.usage()}")
    return 0


def cmd_inspect(args, cfg) -> int:
    for f in args.files:
        p = Path(f)
        print(f"== {p}")
        if p.suffix.lower() in (".html", ".htm", ".xhtml"):
            for line in inspect_html(read_text_any(p), cfg):
                print(line)
        mod = load_module(p, cfg)
        types: dict[str, int] = {}
        for o in mod.objects:
            types[o.object_type or "(boş)"] = types.get(o.object_type or "(boş)", 0) + 1
        levels = sorted({o.level for o in mod.objects})
        print(f"Nesne: {len(mod.objects)}, seviyeler: {levels}")
        print("Object Type dağılımı: " + ", ".join(f"{k}: {v}" for k, v in sorted(types.items(), key=lambda x: -x[1])))
        kind = args.kind
        if kind == "auto":
            d = cfg["doors"]
            is_test = any(_matches(o.object_type, d["step_types"] + d["precondition_types"]) for o in mod.objects)
            kind = "tests" if is_test else "requirements"
            print(f"Dosya türü (otomatik): {'test' if is_test else 'gereksinim'} (değiştirmek için --kind)")
        if kind == "requirements":
            reqs = extract_requirements(mod, cfg)
            print(f"Gereksinim olarak okunan: {len(reqs)} (bağlantı içeren: {sum(1 for r in reqs if r.links)})")
            for r in reqs[: args.show]:
                print(f"  {r.id} [{r.section_text}]: {r.text[:140]!r}" + (f" bağlantı={r.links[:4]}" if r.links else ""))
        else:
            cases = extract_test_cases(mod, cfg)
            print(f"Test case olarak okunan: {len(cases)} ({sum(len(c.steps) for c in cases)} adım, "
                  f"bağlantısı olan: {sum(1 for c in cases if c.links)})")
            for c in cases[: args.show]:
                print(f"  {c.title!r} bağlantı={c.links[:3]} adım={len(c.steps)}: {c.steps[0].object_text[:100]!r}")
            print("  Not: projects.json ile 'library' komutu, test bağlantılarının hangi sütundan alınacağını "
                  "(en çok gereksinime çözülen) otomatik seçer.")
        if args.dump:
            out = Path(args.dump)
            _write(out, json.dumps(mod.to_json(), ensure_ascii=False, indent=1))
            print(f"Okunan yapı yazıldı: {out}")
    return 0


def cmd_library(args, cfg) -> int:
    pf = load_projects(args.projects)
    vocab = Vocabulary()
    vocab.load_paths(pf.signals)
    for spec in pf.projects:
        pcfg = spec.config(cfg)
        pd = load_project(spec, cfg)
        vocab.load_paths(spec.signals)
        vocab.learn_from_cases(pd.cases)
        st = pd.stats()
        print(f"{spec.name}: {json.dumps(st, ensure_ascii=False)}")
        doc_paths = list(pf.docs) + list(spec.docs)
        if doc_paths:
            chunks, objects = load_documents(doc_paths, pcfg, pf.doc_roles)
            kb = KnowledgeBase(chunks, pcfg, None, None, objects)
            linked = [r for r in pd.requirements if kb.linked(r.id, r.links)]
            per_doc: dict[str, int] = {}
            for r in pd.requirements:
                for o in kb.linked(r.id, r.links):
                    per_doc[o.doc] = per_doc.get(o.doc, 0) + 1
            print(f"  dokümanlar: {len(chunks)} bölüm, {len(objects)} ID'li nesne; bağlantısı olan gereksinim "
                  f"{len(linked)}/{len(pd.requirements)}; bağlantı/doküman: {json.dumps(per_doc, ensure_ascii=False)}")
    print(f"Sinyal sözlüğü (tüm projeler): {len(vocab)} sinyal, {len(vocab.simulators)} simülatör")
    return 0


def cmd_prompt(args, cfg) -> int:
    """Shows what the model would receive for one requirement (no chat call)."""
    target, lib, sigs, docs, roles = _resolve_target(args, cfg)
    llm, cache = _llm(cfg), _cache(args)
    ctx = Context(args, cfg, llm, cache, target, lib, sigs, docs, roles)
    reqs = ctx.target_data.requirements
    req = next((r for r in reqs if r.id.upper() == args.id.strip().upper()), None)
    if req is None:
        raise SystemExit(f"{args.id} bulunamadı. Örnek ID'ler: {', '.join(r.id for r in reqs[:5])}")
    from .generate import neighbors_of

    messages, examples, docs_used = ctx.generator.build_messages(
        req, neighbors_of(req, reqs, int(cfg["retrieval"]["neighbor_requirements"])), (target.name,))
    text = "\n\n".join(f"===== {m['role'].upper()} =====\n{m['content']}" for m in messages)
    text += f"\n\n===== KULLANILAN =====\nörnekler: {examples}\ndokümanlar: {docs_used}\n"
    refs = ctx.target_data.cases_by_req.get(req.id)
    if refs:
        text += "\n===== GERÇEK TESTLER (modele GÖNDERİLMEZ, yalnızca karşılaştırma için) =====\n"
        text += json.dumps([c.to_schema() for c in refs], ensure_ascii=False, indent=1)
    if args.out:
        _write(Path(args.out), text)
        print(f"Prompt yazıldı: {args.out} ({len(text)} karakter)")
    else:
        print(text)
    return 0


def cmd_generate(args, cfg) -> int:
    target, lib, sigs, docs, roles = _resolve_target(args, cfg)
    llm, cache = _llm(cfg), _cache(args)
    ctx = Context(args, cfg, llm, cache, target, lib, sigs, docs, roles)
    out = Path(args.out or f"runs/{target.name}_{_stamp()}")
    only = args.only.split(",") if args.only else None
    results = ctx.run(out, only, args.limit, args.force, with_tests_only=args.with_tests_only)
    if ctx.target_data.cases_by_req and not args.no_eval:
        _evaluate_and_report(cfg, llm, cache, ctx.target_data, results, out, args.judge)
    return 0


def cmd_evaluate(args, cfg) -> int:
    pf = load_projects(args.projects)
    spec = pf.get(args.target)
    if not spec.tests:
        raise SystemExit(f"{spec.name} projesinin 'tests' dosyası yok; karşılaştırılacak gerçek test yok")
    run = Path(args.run)
    llm, cache = _llm(cfg), _cache(args)
    _evaluate_and_report(cfg, llm, cache, load_project(spec, cfg), _load_results(run), run, args.judge)
    return 0


def cmd_crossval(args, cfg) -> int:
    pf = load_projects(args.projects)
    names = [n.strip().lower() for n in args.only_projects.split(",")] if args.only_projects else None
    evaluated = [p for p in pf.projects if p.tests and (names is None or p.name.lower() in names)]
    out_root = Path(args.out or f"runs/crossval_{_stamp()}")
    llm, cache = _llm(cfg), _cache(args)
    rows = []
    for target in evaluated:
        lib = pf.example_projects(target)
        if not lib and not args.no_examples:
            print(f"Uyarı: {target.name} için örnek alınacak başka program yok; örneksiz üretilecek.")
        ctx = Context(args, cfg, llm, cache, target, lib, pf.signals, pf.docs, pf.doc_roles)
        out = out_root / target.name
        results = ctx.run(out, None, args.limit, args.force, with_tests_only=True)
        ev = _evaluate_and_report(cfg, llm, cache, ctx.target_data, results, out, args.judge)
        rows.append({"project": target.name, "summary": ev["summary"], "report": f"{target.name}/evaluation.html"})
    _write(out_root / "crossval.json", json.dumps(rows, ensure_ascii=False, indent=1))
    _write(out_root / "crossval.html", crossval_html("Çapraz deneme (leave-one-project-out)", rows))
    print(f"Özet: {out_root / 'crossval.html'}")
    return 0


def cmd_scan(args, cfg) -> int:
    from .discover import scan, write_projects

    out = Path(args.out)
    content = scan(Path(args.data), out.resolve().parent, cfg)
    write_projects(content, out)
    print(f"{len(content['projects'])} proje yazıldı: {out}")
    return 0


def cmd_pack(args, cfg) -> int:
    from .menu import pack

    print(f"Paket: {pack(Path(args.out) if args.out else None)}")
    return 0


# ------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="reqtestgen", description="DOORS gereksinimlerinden test prosedürü taslağı üretir.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--config", help="config.json yolu (varsayılan: ./config.json)")
    ap.add_argument("--env", help=".env yolu (varsayılan: config.json'un yanındaki .env)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="LLM bağlantısını ve modelleri dener")

    p = sub.add_parser("inspect", help="HTML/JSON dosyasında ne okunduğunu gösterir")
    p.add_argument("files", nargs="+")
    p.add_argument("--show", type=int, default=5, help="gösterilecek örnek sayısı")
    p.add_argument("--kind", choices=["auto", "requirements", "tests"], default="auto")
    p.add_argument("--dump", help="okunan yapıyı bu JSON dosyasına yaz")

    p = sub.add_parser("library", help="projects.json'daki projelerin istatistiği")
    p.add_argument("--projects", required=True)

    p = sub.add_parser("scan", help="data klasörünü tarar, projects.json'u otomatik yazar")
    p.add_argument("--data", default="data", help="program klasörlerini içeren klasör")
    p.add_argument("--out", default="projects.json")

    sub.add_parser("menu", help="numaralı menü (baslat.bat bunu açar)")

    p = sub.add_parser("pack", help="iç ağa taşımak için zip paketi oluşturur")
    p.add_argument("--out", default="", help="zip yolu (varsayılan: ../ReqTestGen_<tarih>.zip)")

    def common(p, eval_opts=True):
        p.add_argument("--projects", help="projects.json")
        p.add_argument("--out", help="çıktı klasörü (varsayılan: runs/<proje>_<zaman>)")
        p.add_argument("--limit", type=int, help="en fazla N gereksinim (deneme için)")
        p.add_argument("--force", action="store_true", help="önceki sonuçları kullanmadan yeniden üret")
        p.add_argument("--docs", nargs="*", help="ek sistem dokümanı dosya/klasörleri")
        p.add_argument("--signals", nargs="*", help="ek sinyal kaynakları (model XML klasörü, .txt liste)")
        p.add_argument("--no-examples", action="store_true", help="örnek test kullanma (deney için)")
        p.add_argument("--no-docs", action="store_true", help="sistem dokümanı kullanma (deney için)")
        p.add_argument("--no-signals", action="store_true", help="sinyal sözlüğü kullanma (deney için)")
        p.add_argument("--judge", action="store_true", help="değerlendirmede hakem model kullan (ek LLM çağrısı)")
        p.add_argument("--cache", default="cache", help="embedding önbellek klasörü")

    p = sub.add_parser("generate", help="test üretir (+ gerçek testleri varsa karşılaştırır)")
    common(p)
    p.add_argument("--target", help="projects.json'daki hedef proje adı")
    p.add_argument("--requirements", help="projects.json dışında bir gereksinim dosyası")
    p.add_argument("--only", help="yalnızca bu gereksinim ID'leri (virgülle)")
    p.add_argument("--with-tests-only", action="store_true", help="yalnızca gerçek testi olan gereksinimler")
    p.add_argument("--no-eval", action="store_true", help="karşılaştırma yapma")

    p = sub.add_parser("prompt", help="bir gereksinim için modele gidecek prompt'u gösterir (LLM çağırmaz)")
    common(p)
    p.add_argument("--target", help="projects.json'daki hedef proje adı")
    p.add_argument("--requirements", help="projects.json dışında bir gereksinim dosyası")
    p.add_argument("--id", required=True, help="gereksinim ID'si")

    p = sub.add_parser("evaluate", help="bir çalıştırmayı gerçek testlerle karşılaştırır")
    p.add_argument("--projects", required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--run", required=True, help="generate çıktı klasörü")
    p.add_argument("--judge", action="store_true")
    p.add_argument("--cache", default="cache")

    p = sub.add_parser("crossval", help="her projeyi diğerlerinden öğrenerek üretir ve karşılaştırır")
    common(p)
    p.add_argument("--only-projects", help="yalnızca bu projeler hedef olsun (virgülle)")
    return ap


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config, args.env)
        if args.cmd == "menu":
            from .menu import run_menu

            return run_menu()
        return {"check": cmd_check, "inspect": cmd_inspect, "library": cmd_library, "generate": cmd_generate,
                "prompt": cmd_prompt, "evaluate": cmd_evaluate, "crossval": cmd_crossval, "scan": cmd_scan,
                "pack": cmd_pack}[args.cmd](args, cfg)
    except LLMError as e:
        print(f"LLM hatası: {e}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as e:
        print(f"Hata: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Durduruldu. Aynı komutu tekrar çalıştırınca kaldığı yerden devam eder.", file=sys.stderr)
        return 130
