"""DOORS HTML export -> DoorsModule.

DOORS HTML exports differ per project and per exported view, so nothing is hard-coded:

* the table whose header maps to the most known columns is used;
* columns are mapped by explicit names (config html.columns), then aliases (html.column_aliases),
  then by their content:
    - object type: a column whose values are mostly DOORS object types ("Quality" in some modules)
    - main column: the DOORS main column is usually titled with the module name ("FLIR SSS"),
      so the unmapped column with the most text becomes heading-or-text
    - links: of all columns whose header looks like a link column ("SIT Link(s)", "Out-links ...",
      "Links"), the one with the most values; the others are kept as attributes
* unmapped columns are kept as attributes (Rationale, Verification Method, Configuration ...).

Cell text keeps line breaks (<br>, <p>, <li>), bullet images become "•", other images
"[image: alt]", tables inside a cell become "a | b | c" rows inside the cell text.
Without a usable table, headings and paragraphs are read and a paragraph that starts with an ID
(html.id_pattern) starts a new object. `inspect_html` reports what was found.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .doors import DoorsModule, DoorsObject, fix_hierarchy

_BREAK_TAGS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "ul", "ol"}
_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}


@dataclass
class Cell:
    text: str = ""
    header: bool = False
    bold: bool = False          # all text was bold / strong
    heading_level: int = 0      # <hN> inside the cell


@dataclass
class Table:
    rows: list[list[Cell]] = field(default_factory=list)


@dataclass
class Block:
    tag: str
    text: str


def _clean(text: str) -> str:
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


class _OpenCell:
    def __init__(self, header: bool):
        self.parts: list[str] = []
        self.header = header
        self.bold_chars = 0
        self.total_chars = 0
        self.heading_level = 0

    def close(self) -> Cell:
        return Cell(_clean("".join(self.parts)), self.header,
                    self.total_chars > 0 and self.bold_chars >= self.total_chars, self.heading_level)


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self.blocks: list[Block] = []
        # one entry per open table: (table, the cell open in it or None)
        self._stack: list[list] = []
        self._bold = 0
        self._pre = 0
        self._skip = 0
        self._block: list | None = None         # [tag, parts] outside tables

    @property
    def _cell(self) -> _OpenCell | None:
        return self._stack[-1][1] if self._stack else None

    def _emit(self, s: str) -> None:
        if self._cell is not None:
            self._cell.parts.append(s)
        elif not self._stack and self._block is not None:
            self._block[1].append(s)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
            return
        if tag in ("b", "strong"):
            self._bold += 1
        elif tag == "pre":
            self._pre += 1
        if tag == "table":
            self._stack.append([Table(), None])
            return
        if tag == "img":
            alt = (dict(attrs).get("alt") or "").strip()
            if "bullet" in alt.lower():
                self._emit("\n• ")
            elif alt:
                self._emit(f" [image: {alt}] ")
            return
        if self._stack and tag == "tr":
            self._stack[-1][0].rows.append([])
            return
        if self._stack and tag in ("td", "th"):
            if not self._stack[-1][0].rows:
                self._stack[-1][0].rows.append([])
            self._stack[-1][1] = _OpenCell(tag == "th")
            return
        if self._cell is not None and re.fullmatch(r"h[1-6]", tag):
            self._cell.heading_level = self._cell.heading_level or int(tag[1])
        if tag in _BREAK_TAGS:
            self._emit("\n")
        if not self._stack and tag in _BLOCK_TAGS:
            self._flush_block()
            self._block = [tag, []]

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
            return
        if tag in ("b", "strong"):
            self._bold = max(0, self._bold - 1)
        elif tag == "pre":
            self._pre = max(0, self._pre - 1)
        if tag in ("td", "th") and self._cell is not None:
            self._stack[-1][0].rows[-1].append(self._cell.close())
            self._stack[-1][1] = None
            return
        if tag == "table" and self._stack:
            table, open_cell = self._stack.pop()
            if open_cell is not None:
                table.rows[-1].append(open_cell.close())
            self.tables.append(table)
            if self._cell is not None:  # a table inside a cell: keep its rows in the cell text
                rows = [" | ".join(c.text.replace("\n", " ") for c in r) for r in table.rows if any(c.text for c in r)]
                self._cell.parts.append("\n" + "\n".join(rows) + "\n")
            return
        if tag in _BREAK_TAGS:
            self._emit("\n")
        if not self._stack and tag in _BLOCK_TAGS:
            self._flush_block()

    def handle_data(self, data):
        if self._skip:
            return
        s = data if self._pre else re.sub(r"\s+", " ", data)
        if not s:
            return
        cell = self._cell
        if cell is not None:
            n = len(s.strip())
            cell.total_chars += n
            if self._bold:
                cell.bold_chars += n
            cell.parts.append(s)
        elif not self._stack:
            if self._block is None:
                if not s.strip():
                    return
                self._block = ["p", []]
            self._block[1].append(s)

    def _flush_block(self):
        if self._block is not None:
            text = _clean("".join(self._block[1]))
            if text:
                self.blocks.append(Block(self._block[0], text))
        self._block = None

    def close(self):
        super().close()
        self._flush_block()
        while self._stack:
            table, open_cell = self._stack.pop()
            if open_cell is not None and table.rows:
                table.rows[-1].append(open_cell.close())
            self.tables.append(table)


def parse_html(html: str) -> _Parser:
    p = _Parser()
    p.feed(html)
    p.close()
    return p


# -------------------------------------------------------------- mapping
def _norm_header(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*:]+$", "", (s or "").strip().lower()))


def _type_vocab(cfg: dict) -> set[str]:
    d = cfg["doors"]
    words = set(d["procedure_types"] + d["precondition_types"] + d["step_types"] + d["part_types"]
                + d["ignore_types"] + d["requirement_ignore_types"] + list(d.get("requirement_types") or [])
                + ["requirement", "case", "caption", "ole"])
    return {w.lower() for w in words if w}


def _looks_like_types(values: list[str], cfg: dict) -> bool:
    vals = [v.strip().lower() for v in values if v.strip()]
    if len(vals) < 3:
        return False
    vocab = _type_vocab(cfg)
    hits = sum(1 for v in vals if len(v) <= 40 and any(w in v for w in vocab))
    return hits >= 0.8 * len(vals) and len(set(vals)) <= max(12, len(vals) // 3)


def map_columns(header: list[str], cfg: dict, rows: list[list[Cell]] | None = None) -> dict[str, int]:
    """field -> column index."""
    hcfg = cfg["html"]
    norm = [_norm_header(h) for h in header]
    mapping: dict[str, int] = {}
    for fld, names in (hcfg.get("columns") or {}).items():
        for name in [names] if isinstance(names, str) else names or []:
            if name and _norm_header(name) in norm:
                mapping[fld] = norm.index(_norm_header(name))
                break
    link_re = re.compile(hcfg.get("link_header_pattern") or r"link|verifies|traces?", re.I)

    def column(i: int) -> list[str]:
        return [r[i].text for r in rows or [] if i < len(r)]

    if "frs_links" not in mapping:
        cands = [i for i, h in enumerate(header) if link_re.search(h or "")]
        if cands:
            mapping["frs_links"] = max(cands, key=lambda i: (sum(1 for v in column(i) if v.strip()), -i))
    for fld, aliases in hcfg["column_aliases"].items():
        if fld in mapping:
            continue
        for a in aliases:
            a = _norm_header(a)
            if a in norm and norm.index(a) not in mapping.values():
                mapping[fld] = norm.index(a)
                break
    if rows:
        free = [i for i in range(len(header)) if i not in mapping.values()]
        if "object_type" not in mapping:
            for i in free:
                if _looks_like_types(column(i), cfg):
                    mapping["object_type"] = i
                    free.remove(i)
                    break
        if not ({"object_text", "main"} & set(mapping)) and free:
            best = max(free, key=lambda i: sum(len(v) for v in column(i)))
            if sum(len(v) for v in column(best)) > 0:
                mapping["main"] = best
    return mapping


def _header_row(t: Table) -> int:
    for i, row in enumerate(t.rows[:5]):
        if row and all(c.header for c in row if c.text):
            return i
    return 0


def _choose_table(tables: list[Table], cfg: dict):
    best = None
    for t in tables:
        rows = [r for r in t.rows if r]
        if len(rows) < 2:
            continue
        hi = _header_row(t)
        header = [c.text for c in t.rows[hi]]
        mapping = map_columns(header, cfg, t.rows[hi + 1:])
        if "id" not in mapping or not ({"object_text", "main", "heading"} & set(mapping)):
            continue
        score = len(mapping) * 1000 + len(rows)
        if best is None or score > best[0]:
            best = (score, t, hi, header, mapping)
    return best


_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


def _module_from_table(t: Table, hi: int, header: list[str], mapping: dict[str, int]) -> list[DoorsObject]:
    objs: list[DoorsObject] = []
    for row in t.rows[hi + 1:]:
        if not row or not any(c.text for c in row):
            continue

        def cell(fld: str) -> Cell | None:
            i = mapping.get(fld)
            return row[i] if i is not None and i < len(row) else None

        def val(fld: str) -> str:
            c = cell(fld)
            return c.text if c else ""

        o = DoorsObject(id=val("id"), number=val("number"), object_type=val("object_type"),
                        heading=val("heading"), object_text=val("object_text"),
                        expected_results=val("expected_results"), frs_links=val("frs_links"))
        for fld in ("abs_no", "level"):
            try:
                setattr(o, fld, int(val(fld)))
            except ValueError:
                pass
        main = cell("main")
        if main and main.text:
            typed_heading = "heading" in o.object_type.lower()
            untyped = not o.object_type.strip()
            is_heading = typed_heading or (untyped and (bool(main.heading_level) or (
                main.bold and "\n" not in main.text and len(main.text) < 150 and not o.object_text)))
            if is_heading and not o.heading:
                m = _NUMBERED_HEADING.match(main.text)
                if m and not o.number:
                    o.number, o.heading = m.group(1), m.group(2)
                else:
                    o.heading = main.text
                if main.heading_level and not o.level and not o.number:
                    o.level = main.heading_level
            elif not o.object_text:
                o.object_text = main.text
        for i, c in enumerate(row):
            if i not in mapping.values() and i < len(header) and header[i] and c.text:
                o.attributes[header[i]] = c.text
        objs.append(o)
    return objs


def _module_from_blocks(blocks: list[Block], cfg: dict) -> list[DoorsObject]:
    id_re = re.compile(cfg["html"]["id_pattern"])
    objs: list[DoorsObject] = []
    current: DoorsObject | None = None
    for b in blocks:
        if re.fullmatch(r"h[1-6]", b.tag):
            m = _NUMBERED_HEADING.match(b.text)
            objs.append(DoorsObject(heading=m.group(2) if m else b.text, number=m.group(1) if m else "",
                                    level=int(b.tag[1]), object_type="Heading"))
            current = None
            continue
        m = id_re.match(b.text)
        if m:
            current = DoorsObject(id=m.group(1).strip(), object_text=b.text[m.end():].strip())
            objs.append(current)
        elif current is not None:
            current.object_text = (current.object_text + "\n" + b.text).strip()
    level = 0
    for o in objs:
        if o.object_type == "Heading":
            level = o.level
        elif not o.level:
            o.level = level + 1
    return objs


def module_from_html(html: str, cfg: dict, name: str = "") -> DoorsModule:
    p = parse_html(html)
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    if m:
        title = _clean(re.sub(r"<[^>]+>", " ", m.group(1)))
    chosen = _choose_table(p.tables, cfg)
    if chosen:
        _, t, hi, header, mapping = chosen
        objs = _module_from_table(t, hi, header, mapping)
    else:
        objs = _module_from_blocks(p.blocks, cfg)
    mod = DoorsModule(module=title or name, objects=objs)
    fix_hierarchy(mod)
    return mod


# -------------------------------------------------------------- inspect
def inspect_html(html: str, cfg: dict) -> list[str]:
    p = parse_html(html)
    out = [f"Tablo sayısı: {len(p.tables)}, tablo dışı paragraf/başlık: {len(p.blocks)}"]
    chosen = _choose_table(p.tables, cfg)
    if chosen:
        _, t, hi, header, mapping = chosen
        inv = {v: k for k, v in mapping.items()}
        out.append(f"Kullanılan tablo: {len(t.rows) - hi - 1} satır. Sütunlar:")
        for j, h in enumerate(header):
            filled = sum(1 for r in t.rows[hi + 1:] if j < len(r) and r[j].text.strip())
            out.append(f"  '{h}' -> {inv.get(j, 'özellik (attribute)')}  [{filled} dolu hücre]")
        missing = [f for f in ("id", "object_type", "frs_links") if f not in mapping]
        if not ({"object_text", "main"} & set(mapping)):
            missing.append("object_text/main")
        if missing:
            out.append(f"  Eşlenemeyen alanlar: {missing} -> gerekirse config/projects.json > html.columns ile verin")
    else:
        out.append("ID sütunu olan uygun tablo yok: başlıklar (h1-h6) ve ID ile başlayan paragraflar okunacak "
                   f"(html.id_pattern = {cfg['html']['id_pattern']!r})")
    return out
