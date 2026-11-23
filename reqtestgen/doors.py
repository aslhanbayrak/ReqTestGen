"""DOORS module model.

Inputs are DOORS HTML exports (html_import.py). Internally a module is a flat list of objects
(absNo, id, number, level, objectType, heading, objectText, expectedResults, frsLinks,
parentAbsNo + every other column as an attribute). The same shape can be saved/loaded as JSON
(`convert` command, caching, debugging).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# normalised key -> field name; anything else goes to DoorsObject.attributes
_KEY_ALIASES = {
    "absno": "abs_no", "absolutenumber": "abs_no",
    "id": "id", "identifier": "id", "objectidentifier": "id", "objectid": "id",
    "number": "number", "objectnumber": "number",
    "level": "level", "objectlevel": "level",
    "objecttype": "object_type", "type": "object_type",
    "heading": "heading", "objectheading": "heading",
    "objecttext": "object_text", "text": "object_text",
    "expectedresults": "expected_results", "expectedresult": "expected_results",
    "frslinks": "frs_links", "frslink": "frs_links", "links": "frs_links",
    "parentabsno": "parent_abs_no", "parent": "parent_abs_no",
}


def _norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", k.lower())


@dataclass
class DoorsObject:
    abs_no: int = 0
    id: str = ""
    number: str = ""
    level: int = 0
    object_type: str = ""
    heading: str = ""
    object_text: str = ""
    expected_results: str = ""
    frs_links: str = ""
    parent_abs_no: int = -1
    attributes: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, d: dict) -> "DoorsObject":
        o = cls()
        for k, v in d.items():
            name = _KEY_ALIASES.get(_norm_key(k))
            if k == "attributes" and isinstance(v, dict):
                o.attributes.update(v)
            elif name in ("abs_no", "level", "parent_abs_no"):
                try:
                    setattr(o, name, int(v))
                except (TypeError, ValueError):
                    pass
            elif name:
                setattr(o, name, "" if v is None else str(v))
            else:
                o.attributes[k] = v
        return o

    def to_json(self) -> dict:
        d = {
            "absNo": self.abs_no, "id": self.id, "number": self.number, "level": self.level,
            "objectType": self.object_type, "heading": self.heading, "objectText": self.object_text,
            "expectedResults": self.expected_results, "frsLinks": self.frs_links,
            "parentAbsNo": self.parent_abs_no,
        }
        if self.attributes:
            d["attributes"] = self.attributes
        return d

    @property
    def has_body(self) -> bool:
        return bool(self.object_text.strip() or self.expected_results.strip())


@dataclass
class DoorsModule:
    module: str = ""
    baseline: str = "current"
    exported_at: str = ""
    objects: list[DoorsObject] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"module": self.module, "baseline": self.baseline, "exportedAt": self.exported_at,
                "objects": [o.to_json() for o in self.objects]}


# ------------------------------------------------------------------ I/O
def read_text_any(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    head = raw[:4096].decode("ascii", "ignore")
    m = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)", head, flags=re.I)
    if m:
        try:
            return raw.decode(m.group(1))
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1254")  # Turkish Windows code page (DOORS writes the system code page)


def module_from_json(data, name: str = "") -> DoorsModule:
    if isinstance(data, list):
        data = {"objects": data}
    if not isinstance(data, dict) or not isinstance(data.get("objects"), list):
        raise ValueError("JSON bir DOORS dışa aktarımı değil: 'objects' listesi bekleniyor")
    mod = DoorsModule(module=str(data.get("module") or name), baseline=str(data.get("baseline") or "current"),
                      exported_at=str(data.get("exportedAt") or ""),
                      objects=[DoorsObject.from_json(o) for o in data["objects"] if isinstance(o, dict)])
    fix_hierarchy(mod)
    return mod


def load_module(path: str | Path, cfg: dict) -> DoorsModule:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Dosya bulunamadı: {p}")
    if p.suffix.lower() in (".html", ".htm", ".xhtml"):
        from .html_import import module_from_html

        return module_from_html(read_text_any(p), cfg, name=p.stem)
    if p.suffix.lower() == ".json":
        return module_from_json(json.loads(read_text_any(p)), name=p.stem)
    raise ValueError(f"Desteklenmeyen dosya türü: {p.suffix} (.json, .html, .htm)")


def save_module(mod: DoorsModule, path: str | Path) -> None:
    Path(path).write_text(json.dumps(mod.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------ hierarchy
def level_from_number(number: str) -> int:
    """DOORS numbers: headings "1.6.1" (level 3); other objects "1.6.1.0-2" (level 4, under 1.6.1)."""
    n = (number or "").strip()
    if not n:
        return 0
    if "-" in n:
        head = re.split(r"\.?0-\d+$", n)[0] if re.search(r"0-\d+$", n) else n.split("-")[0]
        parts = [x for x in head.split(".") if x]
        return len(parts) + 1
    return len([x for x in n.split(".") if x])


def fix_hierarchy(mod: DoorsModule) -> None:
    """Fill absNo / level / parentAbsNo when an export lacks them."""
    used = {o.abs_no for o in mod.objects if o.abs_no > 0}
    nxt = (max(used) if used else 0) + 1
    for o in mod.objects:
        if o.abs_no <= 0:
            o.abs_no, nxt = nxt, nxt + 1
    if any(o.level <= 0 for o in mod.objects):
        # Without numbers the nesting of headings is unknown: headings become siblings of the
        # previous heading, other objects children of it.
        last_heading_level = 0
        for o in mod.objects:
            is_heading = bool(o.heading.strip()) and not o.object_text.strip()
            if o.level <= 0:
                o.level = level_from_number(o.number)
            if o.level <= 0:
                o.level = max(1, last_heading_level) if is_heading else last_heading_level + 1
            if is_heading:
                last_heading_level = o.level
    if all(o.parent_abs_no <= 0 for o in mod.objects) and any(o.level > 1 for o in mod.objects):
        stack: dict[int, int] = {}
        for o in mod.objects:
            o.parent_abs_no = stack.get(o.level - 1, -1)
            stack[o.level] = o.abs_no
            for lv in [k for k in stack if k > o.level]:
                del stack[lv]


# ---------------------------------------------------------------- links
def norm_id(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).upper()


def split_links(value: str, cfg: dict) -> list[str]:
    sep = cfg["doors"].get("link_separators") or r"[\r\n,;|]+"
    out = []
    for part in re.split(sep, value or ""):
        p = norm_id(part)
        if p and p not in ("N/A", "NA", "-", "NONE"):
            out.append(p)
    return out


_ID_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)*[-_]\d+\b")


def extract_ids(value: str, cfg: dict) -> list[str]:
    """Link candidates in a cell: the separated parts, plus ID-looking tokens inside them
    ("CC-SSS-1664 CC-SSS-2106", "(CC-SSDD-273 The default value ...)")."""
    out: list[str] = []
    for part in split_links(value, cfg):
        for cand in [part] + [norm_id(t) for t in _ID_TOKEN.findall(part)]:
            if cand not in out and len(cand) <= 80:
                out.append(cand)
    return out


def link_attribute_names(attrs: dict, cfg: dict) -> list[str]:
    pat = re.compile(cfg["html"].get("link_header_pattern") or r"link|verifies|traces?", re.I)
    return [k for k in attrs if pat.search(k)]


class IdIndex:
    """Resolves link strings to requirement IDs: exact match, then a unique suffix/prefix match
    on a separator boundary ("FRS_4920" -> "SMSW_IFF_MGR FRS_4920")."""

    def __init__(self, ids):
        self.ids = {norm_id(i): i for i in ids if i}

    def resolve_any(self, link: str) -> set[str]:
        """The link itself, or else the ID-looking tokens inside it ("FLIR-SSS-12 The display ...")."""
        r = self.resolve(link)
        if r:
            return {r}
        return {x for x in (self.resolve(t) for t in _ID_TOKEN.findall(norm_id(link))) if x}

    def resolve(self, link: str) -> str | None:
        n = norm_id(link)
        if n in self.ids:
            return self.ids[n]
        cands = [orig for k, orig in self.ids.items() if _ends_on_boundary(k, n) or _ends_on_boundary(n, k)]
        return cands[0] if len(cands) == 1 else None


def _ends_on_boundary(longer: str, shorter: str) -> bool:
    return (len(longer) > len(shorter) and longer.endswith(shorter)
            and longer[-len(shorter) - 1] in " _-/:")


# -------------------------------------------------------- classification
def _matches(value: str, keywords) -> bool:
    v = (value or "").strip().lower()
    return bool(v) and any(k and k.lower() in v for k in keywords or [])


def classify_test_object(o: DoorsObject, cfg: dict) -> str:
    """procedure | part | precondition | step | ignore - same order and fallbacks as AVISTA."""
    d = cfg["doors"]
    t = o.object_type
    if _matches(t, d["procedure_types"]):
        return "procedure"
    if _matches(t, d["precondition_types"]):
        return "precondition"
    if _matches(t, d["step_types"]):
        return "step"
    if _matches(t, d["part_types"]):
        return "part"
    if _matches(t, d["ignore_types"]):
        return "ignore"
    if o.heading.strip() and not o.has_body:
        return "part"
    if not o.has_body:
        return "ignore"
    return "step"


# ---------------------------------------------------------- requirements
@dataclass
class Requirement:
    id: str
    text: str
    section: list[str]          # heading path
    number: str = ""
    object_type: str = ""
    order: int = 0              # position in the module
    attributes: dict = field(default_factory=dict)
    links: list[str] = field(default_factory=list)   # raw link candidates from all link columns

    @property
    def section_text(self) -> str:
        return " > ".join(self.section)


def passes_filter(attrs: dict, flt: dict) -> bool:
    """flt: {"<attribute name part>": ["<allowed value part>", ...]}, case-insensitive substrings.
    An attribute the module does not have does not filter."""
    for name, allowed in (flt or {}).items():
        keys = [k for k in attrs if name.lower() in k.lower()]
        if not keys or not allowed:
            continue
        values = " ".join(str(attrs[k]) for k in keys).lower()
        if not any(a.lower() in values for a in ([allowed] if isinstance(allowed, str) else allowed)):
            return False
    return True


def _heading_title(o: DoorsObject) -> str:
    h = o.heading.strip()
    return f"{o.number} {h}".strip() if o.number and not re.match(r"^\d", h) else h


def extract_requirements(mod: DoorsModule, cfg: dict) -> list[Requirement]:
    d = cfg["doors"]
    reqs: list[Requirement] = []
    headings: dict[int, str] = {}
    for o in mod.objects:
        if o.heading.strip():
            headings[o.level] = _heading_title(o)
            for lv in [k for k in headings if k > o.level]:
                del headings[lv]
        text = o.object_text.strip()
        if not text or not o.id.strip():
            continue
        if d["requirement_types"]:
            if not _matches(o.object_type, d["requirement_types"]):
                continue
        elif _matches(o.object_type, d["requirement_ignore_types"]):
            continue
        attrs = dict(o.attributes)
        if not passes_filter(attrs, d.get("requirement_filter")):
            continue
        section = [headings[k] for k in sorted(headings) if k < o.level or (k == o.level and o.heading.strip())]
        if o.expected_results.strip():
            attrs.setdefault("Expected Results", o.expected_results.strip())
        links = extract_ids(o.frs_links, cfg)
        for k in link_attribute_names(attrs, cfg):
            links += [x for x in extract_ids(str(attrs[k]), cfg) if x not in links]
        reqs.append(Requirement(id=o.id.strip(), text=text, section=section, number=o.number,
                                object_type=o.object_type, order=len(reqs), attributes=attrs, links=links))
    return reqs


# ------------------------------------------------------------ test cases
@dataclass
class TestStepData:
    kind: str                   # precondition | step
    id: str
    object_text: str
    expected_results: str
    links: list[str] = field(default_factory=list)
    resolved: set = field(default_factory=set)       # requirement IDs the links resolved to


@dataclass
class TestCase:
    id: str
    title: str
    path: list[str]
    steps: list[TestStepData] = field(default_factory=list)
    links: list[str] = field(default_factory=list)   # union of the case's and its steps' links
    own_links: list[str] = field(default_factory=list)   # links on the Test Part / Test Case object itself
    resolved: set = field(default_factory=set)       # requirement IDs the case's own links resolved to

    def object_ids(self) -> set[str]:
        return {norm_id(x) for x in [self.id] + [s.id for s in self.steps] if x}

    def subset_for(self, req_id: str) -> "TestCase":
        """When the steps carry their own links (one test part verifies several requirements),
        keep the preconditions and the steps linked to req_id."""
        if req_id in self.resolved or not any(s.resolved for s in self.steps if s.kind == "step"):
            return self
        steps = [s for s in self.steps if s.kind == "precondition" or req_id in s.resolved]
        if not any(s.kind == "step" for s in steps):
            return self
        return TestCase(self.id, self.title, self.path, steps, self.links, self.own_links, self.resolved)

    def to_schema(self) -> dict:
        """Same shape the generator is asked to produce (used for few-shot examples)."""
        return {
            "title": self.title,
            "preconditions": [{"object_text": s.object_text, "expected_results": s.expected_results or "N/A"}
                              for s in self.steps if s.kind == "precondition"],
            "steps": [{"object_text": s.object_text, "expected_results": s.expected_results or "N/A"}
                      for s in self.steps if s.kind == "step"],
        }

    def text(self) -> str:
        return "\n".join([self.title] + [f"{s.object_text}\n{s.expected_results}" for s in self.steps])


def extract_test_cases(mod: DoorsModule, cfg: dict, link_column: str = "") -> list[TestCase]:
    """A test case is the run of preconditions/steps under the closest Test Part / Test Case /
    procedure heading. link_column: attribute holding the requirement links ("" = frs_links)."""

    def raw_links(o: DoorsObject) -> list[str]:
        return split_links(str(o.attributes.get(link_column, "")) if link_column else o.frs_links, cfg)

    cases: list[TestCase] = []
    parts: dict[int, DoorsObject] = {}
    current: TestCase | None = None
    for o in mod.objects:
        kind = classify_test_object(o, cfg)
        if kind in ("procedure", "part"):
            if kind == "procedure":
                parts.clear()
            for lv in [k for k in parts if k >= o.level]:
                del parts[lv]
            parts[o.level] = o
            def name(p: DoorsObject) -> str:
                return p.heading.strip() or p.object_text.strip().split("\n")[0][:100] or p.id

            title = name(o)
            path = [name(p) for _, p in sorted(parts.items())][:-1]
            own = raw_links(o)
            current = TestCase(id=o.id, title=title, path=path, links=list(own), own_links=own)
            cases.append(current)
        elif kind in ("precondition", "step"):
            if current is None:
                current = TestCase(id="", title=mod.module or "Test", path=[])
                cases.append(current)
            links = raw_links(o)
            current.steps.append(TestStepData(kind, o.id, o.object_text.strip(), o.expected_results.strip(), links))
            for link in links:
                if link not in current.links:
                    current.links.append(link)
    return [c for c in cases if c.steps]
