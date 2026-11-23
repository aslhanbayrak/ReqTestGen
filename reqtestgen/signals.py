"""Signal vocabulary: which signal names, simulators and enumeration values exist.

Sources
* device model XMLs (root <Device Name DisplayName>, <SubField Name> with
  <Enumerations><Enumerator Name Value/>), the same files AVISTA's SimulatorModelIndex reads
* plain text lists (.txt): one signal name per line
* example tests: lines such as "CC_IFF_OPER_MODE : (2) NORMAL",
  "X is displayed as (1) ERROR", "PVI_MGR simulator;", "Inspect IFF LRM RX"

The line rules follow TestAutomation.Core/Doors/DoorsTextParser.cs so that what is learnt and
checked here matches what AVISTA can import.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .search import tokenize

_HEADER = re.compile(r"^\s*(?:using\s+simulator\s*,?\s*set\s+the\s+followings?|verify\s+that|check\s+that|"
                     r"the\s+followings?)\s*[,:;.]?\s*", re.I)
_SIM_LINE = re.compile(r"^\s*(?P<name>.+?)\s+simulator\s*[;:.]?\s*$", re.I)
_INSPECT = re.compile(r"^\s*Inspect\s+(?P<target>.+?)\s*$", re.I)
_WAIT = re.compile(r"^wait\s+(?:for\s+)?\d+(?:[.,]\d+)?\s*[A-Za-z]*$", re.I)
_DISPLAYED = re.compile(r"^\s*(?P<sig>.+?)\s+(?:is\s+displayed\s+as|displays?\s+as|shall\s+be|is\s+set\s+to)\s+(?P<val>.+)$", re.I)
_PARAM = re.compile(r"^[A-Za-z][A-Za-z0-9]*(_[A-Za-z0-9]+)+$")
_MESSAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ENUM = re.compile(r"^\(\s*(?P<v>[^)]+?)\s*\)\s*(?P<n>.*)$")
_PLACEHOLDER = re.compile(r"^(n\s*/?\s*a|na|none|n\.a\.?|-+|—+)\s*[.;]?$", re.I)


@dataclass
class SignalLine:
    kind: str           # set (object text) | check (expected results)
    message: str
    signal: str
    value: str
    simulator: str
    raw: str


@dataclass
class ParsedText:
    lines: list[SignalLine] = field(default_factory=list)
    simulators: list[str] = field(default_factory=list)


def _logical_lines(text: str):
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\s+(?=Inspect\b)", "\n", t, flags=re.I)
    for para in t.split("\n"):
        for part in re.split(r"\s*;\s*", para):
            p = part.strip()
            if p:
                yield p


def _split_signal(raw: str):
    s = re.sub(r"\s+", "", raw or "")
    if not s:
        return None
    if "." in s:
        head, _, tail = s.rpartition(".")
        if head and tail and _MESSAGE.match(head) and _FIELD.match(tail):
            return head, tail
        return None
    return ("", s) if _PARAM.match(s) else None


def _split_inspect(target: str) -> str:
    t = target.strip().rstrip(".;").strip()
    m = re.match(r"^(?P<n>.+?)\s+(?:TX|RX)\s*$", t, flags=re.I)
    return m.group("n").strip() if m else t


def parse_block(text: str, kind: str) -> ParsedText:
    out = ParsedText()
    sim = ""
    for raw in _logical_lines(text):
        line = raw.strip().rstrip(".;").strip()
        if not line or _PLACEHOLDER.match(line):
            continue
        line = _HEADER.sub("", line, count=1).strip().rstrip(".;").strip()
        if not line or _WAIT.match(line):
            continue
        m = _INSPECT.match(line)
        if m:
            out.simulators.append(_split_inspect(m.group("target")))
            continue
        m = _SIM_LINE.match(line)
        if m:
            sim = m.group("name").strip()
            out.simulators.append(sim)
            continue
        parsed = None
        m = _DISPLAYED.match(line)
        if m and _split_signal(m.group("sig")):
            parsed = (*_split_signal(m.group("sig")), m.group("val"))
        elif ":" in line:
            left, _, right = line.partition(":")
            sp = _split_signal(left)
            if sp:
                parsed = (*sp, right)
        if parsed:
            msg, sig, val = parsed
            out.lines.append(SignalLine(kind, msg, sig, val.strip().rstrip(".;").strip(), sim, raw))
    return out


def parse_step(object_text: str, expected_results: str) -> ParsedText:
    a = parse_block(object_text, "set")
    b = parse_block(expected_results, "check")
    return ParsedText(a.lines + b.lines, a.simulators + b.simulators)


def split_enum(value: str) -> tuple[str, str]:
    """'(2) NORMAL' -> ('2', 'NORMAL'); 'NORMAL' -> ('', 'NORMAL')."""
    m = _ENUM.match(value or "")
    return (m.group("v").strip(), m.group("n").strip()) if m else ("", (value or "").strip())


@dataclass
class Signal:
    name: str
    messages: set = field(default_factory=set)
    simulators: set = field(default_factory=set)
    enums: dict = field(default_factory=dict)       # value -> name
    sources: set = field(default_factory=set)       # model | list | examples

    def describe(self) -> str:
        parts = [self.name]
        if self.simulators:
            parts.append("sim: " + ", ".join(sorted(self.simulators))[:120])
        if self.enums:
            parts.append("values: " + ", ".join(f"({v}) {n}".strip() for v, n in sorted(self.enums.items()))[:300])
        return " | ".join(parts)


class Vocabulary:
    def __init__(self):
        self.signals: dict[str, Signal] = {}
        self.simulators: set[str] = set()

    def __len__(self) -> int:
        return len(self.signals)

    def _get(self, name: str, source: str) -> Signal:
        key = name.upper()
        s = self.signals.get(key)
        if s is None:
            s = self.signals[key] = Signal(name)
        s.sources.add(source)
        return s

    def known(self, name: str) -> bool:
        return name.upper() in self.signals

    # ------------------------------------------------------------ loading
    def load_paths(self, paths) -> None:
        for p in paths or []:
            p = Path(p)
            files = sorted(p.rglob("*")) if p.is_dir() else [p]
            if not p.exists():
                print(f"  Uyarı: sinyal kaynağı bulunamadı: {p}")
            for f in files:
                if f.suffix.lower() == ".xml":
                    self.load_model_xml(f)
                elif f.suffix.lower() in (".txt", ".csv"):
                    self.load_list(f)

    def load_model_xml(self, path: Path) -> int:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            return 0
        if _local(root.tag).lower() != "device":
            return 0
        dev = root.get("DisplayName") or root.get("Name") or path.stem
        self.simulators.add(dev)
        if root.get("Name"):
            self.simulators.add(root.get("Name"))
        parents = {c: p for p in root.iter() for c in p}
        n = 0
        for sf in root.iter():
            if _local(sf.tag) != "SubField" or not (sf.get("Name") or "").strip():
                continue
            s = self._get(sf.get("Name").strip(), "model")
            s.simulators.add(dev)
            p = parents.get(sf)
            while p is not None and (_local(p.tag) == "SubFields" or not p.get("Name")):
                p = parents.get(p)
            if p is not None and p.get("Name"):
                s.messages.add(p.get("Name").strip())
            for en in sf.iter():
                if _local(en.tag) == "Enumerator":
                    v, nm = (en.get("Value") or "").strip(), (en.get("Name") or "").strip()
                    if v or nm:
                        s.enums[v] = nm
            n += 1
        return n

    def load_list(self, path: Path) -> None:
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            name = re.split(r"[,;\t]", line.strip(), maxsplit=1)[0].strip()
            if name and not name.startswith("#") and re.match(r"^[A-Za-z][A-Za-z0-9_.]*$", name):
                self._get(name.rsplit(".", 1)[-1], "list")

    def learn_from_cases(self, cases) -> None:
        for c in cases:
            for st in c.steps:
                parsed = parse_step(st.object_text, st.expected_results)
                self.simulators.update(s for s in parsed.simulators if s)
                for ln in parsed.lines:
                    s = self._get(ln.signal, "examples")
                    if ln.message:
                        s.messages.add(ln.message)
                    if ln.simulator:
                        s.simulators.add(ln.simulator)
                    v, nm = split_enum(ln.value)
                    if v and v not in s.enums:
                        s.enums[v] = nm

    # ---------------------------------------------------------- prompting
    def relevant(self, text: str, limit: int) -> list[Signal]:
        """Signals named in `text` first, then those sharing the most words with it."""
        if not self.signals or limit <= 0:
            return []
        upper = (text or "").upper()
        words = set(tokenize(text))
        scored = []
        for key, s in self.signals.items():
            if re.search(r"(?<![A-Z0-9_])" + re.escape(key) + r"(?![A-Z0-9_])", upper):
                score = 1000.0
            else:
                parts = [p for p in tokenize(s.name) if "_" not in p]
                score = sum(1 for p in parts if p in words) / (len(parts) or 1)
            if score > 0:
                scored.append((score, key))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self.signals[k] for _, k in scored[:limit]]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# -------------------------------------------------------------- checking
def check_test_cases(cases: list[dict], vocab: Vocabulary) -> list[str]:
    """Warnings for one requirement's generated cases (empty vocabulary = nothing to check)."""
    warnings: list[str] = []
    unknown, bad_enum, tbd = set(), set(), set()
    no_check = 0
    for c in cases:
        for st in c.get("preconditions", []) + c.get("steps", []):
            parsed = parse_step(st.get("object_text", ""), st.get("expected_results", ""))
            for ln in parsed.lines:
                if ln.signal.upper().startswith("TBD"):
                    tbd.add(ln.signal)
                    continue
                if len(vocab) and not vocab.known(ln.signal):
                    unknown.add(ln.signal)
                    continue
                sig = vocab.signals.get(ln.signal.upper())
                v, nm = split_enum(ln.value)
                if sig and sig.enums and v:
                    if v not in sig.enums:
                        bad_enum.add(f"{ln.signal} = ({v})")
                    elif nm and sig.enums[v] and nm.upper() != sig.enums[v].upper():
                        bad_enum.add(f"{ln.signal} = ({v}) {nm} (beklenen: ({v}) {sig.enums[v]})")
        for st in c.get("steps", []):
            exp = (st.get("expected_results") or "").strip()
            if not exp or _PLACEHOLDER.match(exp):
                no_check += 1
    if unknown:
        warnings.append("Sözlükte olmayan sinyal: " + ", ".join(sorted(unknown)))
    if bad_enum:
        warnings.append("Geçersiz enum değeri: " + "; ".join(sorted(bad_enum)))
    if tbd:
        warnings.append("Belirsiz (TBD) sinyal: " + ", ".join(sorted(tbd)))
    if no_check:
        warnings.append(f"Beklenen sonucu olmayan {no_check} adım")
    return warnings


def unknown_signals(cases: list[dict], vocab: Vocabulary) -> list[str]:
    if not len(vocab):
        return []
    out = set()
    for c in cases:
        for st in c.get("preconditions", []) + c.get("steps", []):
            for ln in parse_step(st.get("object_text", ""), st.get("expected_results", "")).lines:
                if not ln.signal.upper().startswith("TBD") and not vocab.known(ln.signal):
                    out.add(ln.signal)
    return sorted(out)
