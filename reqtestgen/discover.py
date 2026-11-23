"""Builds projects.json from a folder of DOORS HTML exports, so nobody has to write it by hand.

    data/
      GMH/        test documents, requirement modules, MKSD, SSDD ... (any names)
      HURKUS2/    ...
      ORTAK/      (optional; also COMMON/SHARED) documents shared by every program

Per program folder:
* a file with Test Step / Test Precondition objects is a test document;
* its requirement module is the file in the same program (or ORTAK) that most of its links resolve to;
* every other file of the program is a system document, its role = the module title;
* a program without test documents is a new project; its requirement module is guessed (most
  requirements, verification attributes) and reported, so it can be confirmed.
Projects of one program share a "group": when a project is evaluated, examples from its own program are
not used (no near-duplicate answers), while a new project may learn from every program.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .doors import IdIndex, _matches, extract_requirements, link_attribute_names, load_module, split_links

HTML_SUFFIXES = {".htm", ".html", ".xhtml"}
SHARED_NAMES = {"ortak", "common", "shared", "genel"}


@dataclass
class FileInfo:
    path: Path
    title: str
    is_test: bool
    ids: list = field(default_factory=list)
    links: list = field(default_factory=list)        # all link values (tests only)
    requirements: int = 0
    has_verification: bool = False
    error: str = ""


def _inspect(path: Path, cfg: dict) -> FileInfo:
    try:
        mod = load_module(path, cfg)
    except Exception as e:  # unreadable file: reported, not fatal
        return FileInfo(path, path.stem, False, error=str(e))
    d = cfg["doors"]
    is_test = any(_matches(o.object_type, d["step_types"] + d["precondition_types"]) for o in mod.objects)
    info = FileInfo(path, mod.module or path.stem, is_test, ids=[o.id for o in mod.objects if o.id.strip()])
    if is_test:
        for o in mod.objects:
            info.links += split_links(o.frs_links, cfg)
            for k in link_attribute_names(o.attributes, cfg):
                info.links += split_links(str(o.attributes[k]), cfg)
    else:
        reqs = extract_requirements(mod, cfg)
        info.requirements = len(reqs)
        info.has_verification = any(re.search(r"verification", k, re.I) for r in reqs[:50] for k in r.attributes)
    return info


def _html_files(folder: Path) -> list[Path]:
    return sorted(f for f in folder.rglob("*") if f.is_file() and f.suffix.lower() in HTML_SUFFIXES)


def _rel(p: Path, base: Path) -> str:
    try:
        return p.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return str(p.resolve())


def _name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s).strip("_").upper() or "PROJE"


def scan(data_dir: Path, base: Path, cfg: dict, log=print) -> dict:
    """Returns the projects.json content; paths relative to `base` (the folder of projects.json)."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Klasör bulunamadı: {data_dir}")
    subdirs = sorted(d for d in data_dir.iterdir() if d.is_dir())
    shared_dirs = [d for d in subdirs if d.name.lower() in SHARED_NAMES]
    programs = [d for d in subdirs if d not in shared_dirs]
    if not programs and _html_files(data_dir):
        programs = [data_dir]
    if not programs:
        raise ValueError(f"{data_dir} içinde program klasörü ya da HTML dosyası yok")

    infos: dict[Path, FileInfo] = {}

    def info(f: Path) -> FileInfo:
        if f not in infos:
            log(f"  okunuyor: {_rel(f, base)}")
            infos[f] = _inspect(f, cfg)
            if infos[f].error:
                log(f"    okunamadı: {infos[f].error}")
        return infos[f]

    shared = [info(f) for d in shared_dirs for f in _html_files(d)]
    shared_docs = [i for i in shared if not i.is_test and not i.error]
    out: dict = {"docs": [{"path": _rel(i.path, base), "role": f"{i.title} (ortak)"} for i in shared_docs],
                 "projects": []}

    for prog in programs:
        files = [info(f) for f in _html_files(prog)]
        files = [i for i in files if not i.error]
        tests = [i for i in files if i.is_test and i.ids]
        others = [i for i in files if not i.is_test]
        req_cands = [i for i in others if i.ids]
        group = _name(prog.name)
        log(f"Program {prog.name}: {len(tests)} test dokümanı, {len(others)} diğer doküman")
        used_as_req: set[Path] = set()
        projects = []
        for t in tests:
            best, best_n = None, 0
            for cand in req_cands + [i for i in shared_docs if i.ids]:
                idx = IdIndex(cand.ids)
                n = sum(1 for link in set(t.links) if idx.resolve_any(link))
                if n > best_n:
                    best, best_n = cand, n
            if best is None:
                log(f"  ! {t.path.name}: bağlantıları hiçbir gereksinim dosyasına çözülmedi; atlandı "
                    f"(bağlantı sütunu export'ta görünür mü?)")
                continue
            used_as_req.add(best.path)
            projects.append((t, best, best_n))
        for t, req, n in projects:
            name = group if len(projects) == 1 else f"{group}_{_name(t.path.stem)}"
            docs = [i for i in others if i.path != req.path and i.path not in used_as_req]
            out["projects"].append({
                "name": name, "group": group,
                "requirements": _rel(req.path, base), "tests": _rel(t.path, base),
                "docs": [{"path": _rel(i.path, base), "role": i.title} for i in docs]})
            log(f"  + {name}: test {t.path.name} -> gereksinim {req.path.name} ({n} bağlantı eşleşti), "
                f"{len(docs)} doküman")
        if not projects:
            cands = sorted([i for i in req_cands if i.requirements], key=lambda i: (i.has_verification, i.requirements),
                           reverse=True)
            if not cands:
                log(f"  ! {prog.name}: gereksinim dosyası bulunamadı")
                continue
            req = cands[0]
            out["projects"].append({
                "name": group, "group": group, "requirements": _rel(req.path, base),
                "requirements_guess": [_rel(i.path, base) for i in cands[:6]],
                "docs": [{"path": _rel(i.path, base), "role": i.title} for i in others if i is not req]})
            log(f"  + {group} (yeni proje, test yok): gereksinim dosyası TAHMİNİ {req.path.name} "
                f"({req.requirements} gereksinim)")
    return out


def write_projects(content: dict, path: Path) -> None:
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
