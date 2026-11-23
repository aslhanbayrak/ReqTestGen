"""System documents (SSDD, MKSD, ICD, GMFD, MKB, test environment ...) as prompt context.

Two kinds of files under the configured paths (.html/.htm/.xhtml/.txt/.md):

* DOORS module exports (an HTML table with an ID column): every object becomes an "object chunk"
  with its ID, type, heading path and link columns. Consecutive short objects of one section are
  merged up to `chunk_chars`. Objects are also kept in an ID index, so the generator can follow
  traceability links: requirement -> linked design objects (its "Links"/"Out-links" columns) and
  design objects that link back to the requirement ("All Out Links" in MKSD/SSDD pointing to SSS).
* Other documents: split at headings (h1-h6 / numbered text lines) and cut into chunks.

A document can have a role ("MKSD - screen formats", "GMFD ..."), shown to the model with every
chunk, because names differ per program.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .doors import IdIndex, extract_ids, link_attribute_names, norm_id, read_text_any
from .html_import import _choose_table, module_from_html, parse_html
from .search import EmbeddingCache, SearchIndex

DOC_SUFFIXES = {".html", ".htm", ".xhtml", ".txt", ".md"}


@dataclass
class Chunk:
    doc: str
    section: str
    text: str
    role: str = ""
    ids: list = field(default_factory=list)        # DOORS object IDs inside the chunk

    def label(self) -> str:
        doc = f"{self.doc} ({self.role})" if self.role else self.doc
        return f"{doc} - {self.section}" if self.section else doc

    def render(self) -> str:
        return f"[{self.label()}]\n{self.text}"


@dataclass
class DocObject:
    id: str
    doc: str
    role: str
    section: str
    object_type: str
    text: str
    links: list = field(default_factory=list)

    def render(self) -> str:
        head = f"[{self.doc} ({self.role})" if self.role else f"[{self.doc}"
        head += f" - {self.section}]" if self.section else "]"
        t = f" ({self.object_type})" if self.object_type else ""
        return f"{head}\n{self.id}{t}: {self.text}"


# ------------------------------------------------------------ plain docs
def _sections_from_html(html: str) -> list[tuple[str, str]]:
    p = parse_html(html)
    sections: list[tuple[str, str]] = []
    path: dict[int, str] = {}
    body: list[str] = []

    def flush():
        if body:
            sections.append((" > ".join(path[k] for k in sorted(path)), "\n".join(body)))
            body.clear()

    for b in p.blocks:
        if re.fullmatch(r"h[1-6]", b.tag):
            flush()
            lv = int(b.tag[1])
            path[lv] = b.text
            for k in [k for k in path if k > lv]:
                del path[k]
        else:
            body.append(b.text)
    flush()
    for t in p.tables:
        rows = [" | ".join(c.text.replace("\n", " ") for c in r) for r in t.rows if any(c.text for c in r)]
        if rows:
            sections.append(("Tablo", "\n".join(rows)))
    return sections


_TEXT_HEADING = re.compile(r"^(#{1,6}\s+.+|\d+(?:\.\d+)*\.?\s+[A-ZÇĞİÖŞÜ].{0,120})$")


def _sections_from_text(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading, body = "", []
    for line in text.splitlines():
        s = line.strip()
        if _TEXT_HEADING.match(s) and len(s) < 130:
            if body:
                sections.append((heading, "\n".join(body).strip()))
            heading, body = s.lstrip("# ").strip(), []
        else:
            body.append(line)
    if "".join(body).strip():
        sections.append((heading, "\n".join(body).strip()))
    return sections


def _split(text: str, size: int) -> list[str]:
    paras = [p for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        while len(p) > size:  # a single huge paragraph / table
            if cur:
                out.append(cur)
                cur = ""
            out.append(p[:size])
            p = p[size:]
        if len(cur) + len(p) + 1 > size and cur:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur.strip():
        out.append(cur)
    return out


# ----------------------------------------------------------- DOORS docs
def _is_test_module(mod) -> bool:
    return any(re.search(r"\btest\b", o.object_type, re.I) for o in mod.objects)


def _doors_objects(html: str, doc: str, role: str, cfg: dict) -> list[DocObject] | None:
    """None = a test module (it would show the model the answers, so it is not used as a document)."""
    mod = module_from_html(html, cfg, name=doc)
    if _is_test_module(mod):
        print(f"  Uyarı: {doc} bir test modülü (Test Step/Test Case nesneleri var); doküman olarak kullanılmadı.")
        return None
    out: list[DocObject] = []
    headings: dict[int, str] = {}
    for o in mod.objects:
        if o.heading.strip():
            headings[o.level] = f"{o.number} {o.heading}".strip()
            for lv in [k for k in headings if k > o.level]:
                del headings[lv]
        body = "\n".join(x for x in (o.object_text.strip(), o.expected_results.strip()) if x)
        if not body or not o.id.strip() or re.fullmatch(r"(\s*\[image:[^\]]*\]\s*)+", body):
            continue
        section = " > ".join(headings[k] for k in sorted(headings) if k < o.level or k == o.level and o.heading)
        extra = [f"{k}: {v}" for k, v in o.attributes.items()
                 if isinstance(v, str) and v.strip() and re.search(r"rationale|configuration|allocation", k, re.I)]
        links = extract_ids(o.frs_links, cfg)
        for k in link_attribute_names(o.attributes, cfg):
            links += [x for x in extract_ids(str(o.attributes[k]), cfg) if x not in links]
        out.append(DocObject(o.id.strip(), doc, role, section, o.object_type.strip(),
                             body + ("\n" + "\n".join(extra) if extra else ""), links))
    return out


def _object_chunks(objs: list[DocObject], size: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for o in objs:
        t = f"{o.id}" + (f" ({o.object_type})" if o.object_type else "") + f": {o.text}"
        last = chunks[-1] if chunks else None
        if last and last.doc == o.doc and last.section == o.section and len(last.text) + len(t) + 1 <= size:
            last.text += "\n" + t
            last.ids.append(o.id)
        else:
            for part in _split(t, size):
                chunks.append(Chunk(o.doc, o.section, part, o.role, [o.id]))
    return chunks


def _files(paths) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.suffix.lower() in DOC_SUFFIXES))
        elif p.is_file():
            files.append(p)
        else:
            print(f"  Uyarı: doküman yolu bulunamadı: {p}")
    return files


def _role_of(f: Path, roles: dict) -> str:
    for parent in [f] + list(f.parents):
        r = roles.get(str(parent.resolve()))
        if r:
            return r
    return ""


def load_documents(paths, cfg: dict, roles: dict | None = None) -> tuple[list[Chunk], list[DocObject]]:
    size = int(cfg["knowledge"]["chunk_chars"])
    chunks: list[Chunk] = []
    objects: list[DocObject] = []
    for f in _files(paths):
        role = _role_of(f, roles or {})
        text = read_text_any(f)
        if f.suffix.lower() in (".html", ".htm", ".xhtml"):
            if _choose_table(parse_html(text).tables, cfg):
                objs = _doors_objects(text, f.name, role, cfg)
                if objs is None:
                    continue
                if objs:
                    objects += objs
                    chunks += _object_chunks(objs, size)
                    continue
            secs = _sections_from_html(text)
        else:
            secs = _sections_from_text(text)
        for section, body in secs:
            for part in _split(body, size):
                if len(part.strip()) >= 40:
                    chunks.append(Chunk(f.name, section, part.strip(), role))
    return chunks, objects


def load_chunks(paths, chunk_chars: int = 1800) -> list[Chunk]:
    """Plain loading without a config (kept for scripts); DOORS tables are read as plain tables."""
    from .config import DEFAULTS
    import copy

    cfg = copy.deepcopy(DEFAULTS)
    cfg["knowledge"]["chunk_chars"] = chunk_chars
    return load_documents(paths, cfg)[0]


class KnowledgeBase:
    def __init__(self, chunks: list[Chunk], cfg: dict, llm=None, cache: EmbeddingCache | None = None,
                 objects: list[DocObject] | None = None):
        self.cfg = cfg
        self.chunks = chunks
        self.objects = {norm_id(o.id): o for o in objects or []}
        self.id_index = IdIndex([o.id for o in objects or []])
        self.backlinks: dict[str, list[str]] = {}       # link target (raw, normalised) -> object ids
        for o in objects or []:
            for link in o.links:
                self.backlinks.setdefault(norm_id(link), []).append(o.id)
        self.index = SearchIndex([c.render() for c in chunks], cfg, llm, cache, label="sistem dokümanları") if chunks else None

    def __len__(self) -> int:
        return len(self.chunks)

    def search(self, query: str, k: int) -> list[Chunk]:
        if not self.index or k <= 0:
            return []
        return [self.chunks[i] for i, _ in self.index.search(query, k)]

    def linked(self, req_id: str, links: list[str]) -> list[DocObject]:
        """Objects reachable from the requirement through traceability links (both directions)."""
        kc = self.cfg["knowledge"]
        if not self.objects or not kc.get("follow_links", True):
            return []
        depth, limit = max(1, int(kc.get("link_depth", 1))), int(kc.get("max_linked", 8))
        seen = {norm_id(req_id)}
        out: list[DocObject] = []
        frontier = [(req_id, links)]
        for _ in range(depth):
            nxt = []
            for src_id, src_links in frontier:
                targets = [self.id_index.resolve(x) for x in src_links]
                targets += self.backlinks.get(norm_id(src_id), [])
                short = norm_id(src_id).split(" ")[-1]
                if short != norm_id(src_id):
                    targets += self.backlinks.get(short, [])
                for t in targets:
                    if not t or norm_id(t) in seen:
                        continue
                    seen.add(norm_id(t))
                    o = self.objects[norm_id(t)]
                    out.append(o)
                    nxt.append((o.id, o.links))
                    if len(out) >= limit:
                        return out
            frontier = nxt
        return out
