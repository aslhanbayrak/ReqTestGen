"""Projects and the example library.

A project = requirements export + (optional) tests export + (optional) system documents +
(optional) signal sources. Projects are listed in a projects.json; paths are relative to it:

    {
      "signals": ["models/"],                       # shared by all projects (optional)
      "docs": ["common_docs/"],                     # shared by all projects (optional)
      "projects": [
        {"name": "ProjeA", "requirements": "A/srs.html", "tests": "A/stp.html",
         "docs": ["A/docs/", {"path": "A/ssdd.htm", "role": "SSDD - system/subsystem design"}],
         "signals": ["A/models/"], "context": "A/context.txt",
         "html": {"columns": {"frs_links": "Verifies"}},          # per-project overrides
         "doors": {"requirement_types": ["Requirement"]}},
        {"name": "YeniProje", "requirements": "Yeni/srs.html", "docs": ["Yeni/docs/"]}
      ]
    }

A project without "tests" is a target only (a new project). Example pairs come from the tests'
link attribute (FRS Link(s)): every requirement that at least one test case links to becomes
"requirement -> its test cases".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import _merge
from .doors import (IdIndex, Requirement, TestCase, extract_requirements, extract_test_cases, link_attribute_names,
                    load_module, norm_id)
from .search import EmbeddingCache, SearchIndex


@dataclass
class ProjectSpec:
    name: str
    requirements: Path
    tests: Path | None = None
    docs: list[Path] = field(default_factory=list)
    signals: list[Path] = field(default_factory=list)
    context: Path | None = None
    overrides: dict = field(default_factory=dict)
    group: str = ""              # program; evaluated projects get no examples from their own group

    def config(self, cfg: dict) -> dict:
        return _merge(cfg, self.overrides) if self.overrides else cfg


@dataclass
class ProjectsFile:
    projects: list[ProjectSpec]
    signals: list[Path]          # shared signal sources
    docs: list[Path]             # shared system documents
    doc_roles: dict = field(default_factory=dict)   # resolved path -> role ("SSDD - design", "MKB ...")

    def example_projects(self, target: ProjectSpec) -> list[ProjectSpec]:
        """Projects whose tests serve as examples for `target`. When the target has tests (evaluation),
        its whole group (program) is left out, so no near-copy of its own tests can be shown."""
        return [p for p in self.projects if p.tests and p.name != target.name
                and not (target.tests and target.group and p.group == target.group)]

    def get(self, name: str) -> ProjectSpec:
        for p in self.projects:
            if p.name.lower() == name.lower():
                return p
        raise ValueError(f"Proje bulunamadı: {name} (tanımlı: {', '.join(p.name for p in self.projects)})")


def _as_list(v) -> list:
    return [v] if isinstance(v, str) else list(v or [])


def load_projects(path: str | Path) -> ProjectsFile:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    base = path.parent
    roles: dict[str, str] = {}

    def rel(p):
        return (base / p).resolve()

    def docs(v) -> list[Path]:
        out = []
        for d in _as_list(v):
            if isinstance(d, dict):
                if not d.get("path"):
                    raise ValueError(f"{path}: doküman girdisinde 'path' yok: {d}")
                p = rel(d["path"])
                if d.get("role"):
                    roles[str(p)] = str(d["role"])
            else:
                p = rel(d)
            out.append(p)
        return out

    specs = []
    for p in data.get("projects", []):
        if not p.get("name") or not p.get("requirements"):
            raise ValueError(f"{path}: her projede 'name' ve 'requirements' olmalı")
        specs.append(ProjectSpec(
            name=p["name"], requirements=rel(p["requirements"]),
            tests=rel(p["tests"]) if p.get("tests") else None,
            docs=docs(p.get("docs")), signals=[rel(s) for s in _as_list(p.get("signals"))],
            context=rel(p["context"]) if p.get("context") else None,
            overrides={k: p[k] for k in ("html", "doors") if k in p}, group=str(p.get("group") or p["name"])))
    names = [s.name.lower() for s in specs]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: proje adları benzersiz olmalı")
    return ProjectsFile(specs, [rel(s) for s in _as_list(data.get("signals"))], docs(data.get("docs")), roles)


@dataclass
class ProjectData:
    spec: ProjectSpec
    requirements: list[Requirement]
    cases: list[TestCase]
    cases_by_req: dict[str, list[TestCase]]        # requirement id -> linked test cases
    unmatched_links: list[str]
    cases_without_links: int
    link_column: str = "frs_links"

    @property
    def name(self) -> str:
        return self.spec.name

    def stats(self) -> dict:
        return {
            "test_link_column": self.link_column if self.spec.tests else "",
            "requirements": len(self.requirements),
            "requirements_with_tests": len(self.cases_by_req),
            "test_cases": len(self.cases),
            "test_cases_without_links": self.cases_without_links,
            "unmatched_links": len(self.unmatched_links),
            "unmatched_link_examples": self.unmatched_links[:10],
        }


def _resolve_cases(cases: list[TestCase], index: IdIndex) -> int:
    """Fill .resolved on cases and steps; returns the number of cases linked to a requirement."""
    linked = 0
    for c in cases:
        c.resolved = {r for link in c.own_links for r in index.resolve_any(link)}
        for s in c.steps:
            s.resolved = {r for link in s.links for r in index.resolve_any(link)}
        if c.resolved or any(s.resolved for s in c.steps):
            linked += 1
    return linked


def load_project(spec: ProjectSpec, cfg: dict) -> ProjectData:
    pcfg = spec.config(cfg)
    reqs = extract_requirements(load_module(spec.requirements, pcfg), pcfg)
    cases: list[TestCase] = []
    by_req: dict[str, list[TestCase]] = {}
    unmatched: list[str] = []
    no_links = 0
    link_column = ""
    if spec.tests:
        tmod = load_module(spec.tests, pcfg)
        index = IdIndex([r.id for r in reqs])
        # The test module may have several link columns ("SIT Link(s)" and "Links"): keep the one
        # whose links resolve to most requirements.
        best = None
        cands = [""] + sorted({k for o in tmod.objects for k in link_attribute_names(o.attributes, pcfg)})
        if (pcfg["html"].get("columns") or {}).get("frs_links"):
            cands = [""]
        for col in cands:
            cs = extract_test_cases(tmod, pcfg, link_column=col)
            n = _resolve_cases(cs, index)
            if best is None or n > best[0]:
                best = (n, col, cs)
        _, link_column, cases = best
        for c in cases:
            rids = set(c.resolved) | {r for s in c.steps for r in s.resolved}
            if not c.links and not any(s.links for s in c.steps):
                no_links += 1
            for link in c.links:
                if not index.resolve_any(link) and link not in unmatched:
                    unmatched.append(link)
            for rid in rids:
                by_req.setdefault(rid, []).append(c.subset_for(rid))
        # Links written on the requirement side (a requirement's "Links" column listing test objects).
        owner = {oid: c for c in cases for oid in c.object_ids()}
        for r in reqs:
            for link in r.links:
                c = owner.get(norm_id(link))
                if c is not None and all(x.id != c.id or x.title != c.title for x in by_req.get(r.id, [])):
                    by_req.setdefault(r.id, []).append(c.subset_for(r.id))
    pd = ProjectData(spec, reqs, cases, by_req, unmatched, no_links)
    pd.link_column = link_column or "frs_links"
    return pd


@dataclass
class ExamplePair:
    project: str
    requirement: Requirement
    cases: list[TestCase]

    def query_text(self) -> str:
        return f"{self.requirement.section_text}\n{self.requirement.text}"


def requirement_query(req: Requirement) -> str:
    return f"{req.section_text}\n{req.text}"


class ExampleLibrary:
    def __init__(self, projects: list[ProjectData], cfg: dict, llm=None, cache: EmbeddingCache | None = None):
        self.pairs: list[ExamplePair] = []
        for pd in projects:
            for req in pd.requirements:
                cs = pd.cases_by_req.get(req.id)
                if cs:
                    self.pairs.append(ExamplePair(pd.name, req, cs))
        self.index = SearchIndex([p.query_text() for p in self.pairs], cfg, llm, cache, label="örnek testler") \
            if self.pairs else None

    def __len__(self) -> int:
        return len(self.pairs)

    def projects(self) -> list[str]:
        return sorted({p.project for p in self.pairs})

    def search(self, req: Requirement, k: int, exclude_projects=(), exclude_ids=()) -> list[tuple[ExamplePair, float]]:
        if not self.index:
            return []
        ex_p = set(exclude_projects)
        ex_i = {norm_id(i) for i in exclude_ids}

        def allowed(i: int) -> bool:
            p = self.pairs[i]
            return p.project not in ex_p and norm_id(p.requirement.id) not in ex_i

        return [(self.pairs[i], s) for i, s in self.index.search(requirement_query(req), k, allowed)]
