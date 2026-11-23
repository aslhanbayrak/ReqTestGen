"""Test generation: one LLM conversation per requirement.

prompt = rules + example requirement/test pairs (retrieved) + known signals + system-document
excerpts + neighbouring requirements + the target requirement. The reply must be JSON; an
unusable reply gets one repair round, unknown signal names one fix round. Every result is
saved to <out>/items/<id>.json, so an interrupted run continues where it stopped.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .doors import Requirement, link_attribute_names
from .knowledge import KnowledgeBase
from .library import ExampleLibrary, requirement_query
from .llm import LLMClient, LLMError, extract_json
from .prompts import REPAIR_PROMPT, SIGNAL_FIX_PROMPT, SYSTEM_PROMPT
from .signals import Vocabulary, check_test_cases, unknown_signals

CATEGORIES = {"normal", "robustness", "boundary"}


@dataclass
class GenerationResult:
    requirement_id: str
    status: str = "ok"                      # ok | error
    analysis: str = ""
    test_cases: list = field(default_factory=list)
    open_issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    examples: list = field(default_factory=list)   # [{project, requirement_id, score}]
    docs: list = field(default_factory=list)       # ["doc - section"]
    error: str = ""
    seconds: float = 0.0

    @classmethod
    def from_json(cls, d: dict) -> "GenerationResult":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")[:120] or "item"


# ------------------------------------------------------------ normalising
def _text(v) -> str:
    if isinstance(v, list):
        return "\n".join(_text(x) for x in v)
    return "" if v is None else str(v).strip()


def _steps(v) -> list[dict]:
    out = []
    for s in v or []:
        if isinstance(s, str):
            s = {"object_text": s}
        if not isinstance(s, dict):
            continue
        ot = _text(s.get("object_text") or s.get("action") or s.get("objectText") or s.get("step"))
        er = _text(s.get("expected_results") or s.get("expected") or s.get("expectedResults") or s.get("expected_result"))
        if ot or er:
            out.append({"object_text": ot, "expected_results": er or "N/A"})
    return out


def normalize_reply(data: dict) -> dict:
    cases = data.get("test_cases") or data.get("tests") or data.get("testCases") or []
    if not isinstance(cases, list):
        raise ValueError("'test_cases' bir liste olmalı")
    norm = []
    for c in cases:
        if not isinstance(c, dict):
            continue
        cat = _text(c.get("category") or c.get("type")).lower()
        norm.append({
            "title": _text(c.get("title") or c.get("name") or c.get("objective")) or "Test",
            "category": cat if cat in CATEGORIES else ("robustness" if "robust" in cat else "normal"),
            "preconditions": _steps(c.get("preconditions")),
            "steps": _steps(c.get("steps")),
        })
    norm = [c for c in norm if c["steps"] or c["preconditions"]]
    if not norm:
        raise ValueError("'test_cases' boş ya da hiçbir test adımı içermiyor")
    issues = data.get("open_issues") or []
    return {"analysis": _text(data.get("analysis")), "test_cases": norm,
            "open_issues": [_text(i) for i in (issues if isinstance(issues, list) else [issues]) if _text(i)]}


# ---------------------------------------------------------------- prompt
def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + " ..."


def prompt_attributes(req: Requirement, cfg: dict) -> dict:
    """Requirement attributes shown to the model. Link columns are never shown: on the requirement
    side they list the linked test objects (with their text) and would leak the answer."""
    wanted = [w.lower() for w in cfg["generation"].get("prompt_attributes") or []]
    links = set(link_attribute_names(req.attributes, cfg))
    out = {}
    for k, v in req.attributes.items():
        if k in links or not isinstance(v, str) or not v.strip():
            continue
        if not wanted or any(w in k.lower() for w in wanted):
            out[k] = _truncate(v.strip(), 1500)
    return out


class Generator:
    def __init__(self, cfg: dict, llm: LLMClient, library: ExampleLibrary | None, vocab: Vocabulary,
                 knowledge: KnowledgeBase | None = None, context_text: str = ""):
        self.cfg, self.llm, self.library, self.vocab = cfg, llm, library, vocab
        self.knowledge = knowledge
        self.context_text = context_text

    def build_messages(self, req: Requirement, neighbors: list[Requirement], exclude_projects=()):
        rc, gc, kc = self.cfg["retrieval"], self.cfg["generation"], self.cfg["knowledge"]
        parts: list[str] = []
        examples_used, docs_used = [], []

        if self.context_text:
            parts.append("PROJECT CONTEXT\n" + self.context_text.strip())

        hits = self.library.search(req, int(rc["k"]), exclude_projects=exclude_projects) if self.library else []
        budget = int(rc["max_example_chars"])
        ex_texts = []
        for n, (pair, score) in enumerate(hits, 1):
            tests = [c.to_schema() for c in pair.cases[: int(rc["max_cases_per_example"])]]
            block = (f"### Example {n} (project {pair.project}, requirement {pair.requirement.id})\n"
                     f"Section: {pair.requirement.section_text}\nRequirement: {pair.requirement.text}\n"
                     f"Approved test cases:\n{json.dumps(tests, ensure_ascii=False, indent=1)}")
            if ex_texts and sum(len(t) for t in ex_texts) + len(block) > budget:
                break
            ex_texts.append(_truncate(block, budget))
            examples_used.append({"project": pair.project, "requirement_id": pair.requirement.id,
                                  "score": round(score, 4)})
        if ex_texts:
            parts.append("EXAMPLES (requirement -> the organisation's approved test cases for it)\n\n"
                         + "\n\n".join(ex_texts))
        else:
            parts.append("EXAMPLES: none available. Use the conventions described in the rules.")

        query = requirement_query(req)
        sigs = self.vocab.relevant(query + "\n" + "\n".join(ex_texts), int(gc["max_signals"]))
        if sigs:
            parts.append("KNOWN SIGNALS (name | simulator | enumeration values)\n" + "\n".join(s.describe() for s in sigs))

        if self.knowledge and len(self.knowledge):
            budget, used = int(kc["max_chars"]), 0
            linked_txt, seen_ids = [], set()
            for o in self.knowledge.linked(req.id, req.links):
                t = _truncate(o.render(), budget // 3)
                if linked_txt and used + len(t) > budget:
                    break
                linked_txt.append(t)
                used += len(t)
                seen_ids.add(o.id)
                docs_used.append(f"{o.doc} - {o.id} (bağlantı)")
            kb = []
            for ch in self.knowledge.search(query, int(kc["k"])):
                if ch.ids and set(ch.ids) <= seen_ids:
                    continue
                t = ch.render()
                if (kb or linked_txt) and used + len(t) > budget:
                    break
                kb.append(_truncate(t, budget))
                used += len(t)
                docs_used.append(ch.label())
            if linked_txt:
                parts.append("TRACE-LINKED DOCUMENT OBJECTS (design / interface / screen definitions linked to the "
                             "target requirement in DOORS - use them for exact names, values, colours, keys, pages)\n\n"
                             + "\n\n".join(linked_txt))
            if kb:
                parts.append("OTHER SYSTEM DOCUMENT EXCERPTS (found by similarity, for understanding)\n\n"
                             + "\n\n".join(kb))

        if neighbors:
            parts.append("NEIGHBOURING REQUIREMENTS (context only, do not test them)\n"
                         + "\n".join(f"- {r.id}: {_truncate(r.text, 400)}" for r in neighbors))

        parts.append(f"TARGET REQUIREMENT\nID: {req.id}\nSection: {req.section_text}\nText:\n{req.text}"
                     + "".join(f"\n{k}: {v}" for k, v in prompt_attributes(req, self.cfg).items()))
        parts.append("Write the test cases for the target requirement. Return only the JSON object.")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(parts)}]
        return messages, examples_used, docs_used

    # ------------------------------------------------------------- one item
    def generate(self, req: Requirement, neighbors: list[Requirement], exclude_projects=()) -> GenerationResult:
        t0 = time.time()
        res = GenerationResult(req.id)
        try:
            messages, res.examples, res.docs = self.build_messages(req, neighbors, exclude_projects)
            reply = self.llm.chat(messages)
            try:
                out = normalize_reply(extract_json(reply))
            except (ValueError, json.JSONDecodeError) as e:
                messages += [{"role": "assistant", "content": reply},
                             {"role": "user", "content": REPAIR_PROMPT.format(error=e)}]
                reply = self.llm.chat(messages)
                out = normalize_reply(extract_json(reply))

            unknown = unknown_signals(out["test_cases"], self.vocab)
            if unknown and self.cfg["generation"].get("fix_unknown_signals"):
                cands = self.vocab.relevant(" ".join(unknown) + "\n" + req.text, 40)
                fix = SIGNAL_FIX_PROMPT.format(names=", ".join(unknown),
                                               candidates="\n".join(s.describe() for s in cands) or "(none)")
                try:
                    fixed = normalize_reply(extract_json(self.llm.chat(
                        messages + [{"role": "assistant", "content": reply}, {"role": "user", "content": fix}])))
                    if len(unknown_signals(fixed["test_cases"], self.vocab)) < len(unknown):
                        out = fixed
                except (ValueError, json.JSONDecodeError, LLMError):
                    pass
            res.analysis, res.test_cases, res.open_issues = out["analysis"], out["test_cases"], out["open_issues"]
            res.warnings = check_test_cases(res.test_cases, self.vocab)
        except LLMError as e:
            if e.fatal:
                raise
            res.status, res.error = "error", str(e)
        except (ValueError, json.JSONDecodeError) as e:
            res.status, res.error = "error", f"Model cevabı kullanılamadı: {e}"
        res.seconds = round(time.time() - t0, 1)
        return res


# ------------------------------------------------------------------ run
def neighbors_of(req: Requirement, reqs: list[Requirement], n: int) -> list[Requirement]:
    if n <= 0:
        return []
    same = [r for r in reqs if r.section == req.section]
    i = next((k for k, r in enumerate(same) if r is req), 0)
    return same[max(0, i - n):i] + same[i + 1:i + 1 + n]


def select_requirements(reqs: list[Requirement], only=None, limit: int | None = None) -> list[Requirement]:
    if only:
        wanted = {x.strip().upper() for x in only}
        reqs = [r for r in reqs if r.id.upper() in wanted]
    return reqs[:limit] if limit else reqs


def run_generation(gen: Generator, reqs_all: list[Requirement], targets: list[Requirement], out_dir: Path,
                   exclude_projects=(), force: bool = False) -> list[GenerationResult]:
    items = out_dir / "items"
    items.mkdir(parents=True, exist_ok=True)
    n_neighbors = int(gen.cfg["retrieval"]["neighbor_requirements"])
    results: dict[int, GenerationResult] = {}
    todo = []
    for i, r in enumerate(targets):
        f = items / f"{safe_name(r.id)}.json"
        if f.is_file() and not force:
            prev = GenerationResult.from_json(json.loads(f.read_text(encoding="utf-8")))
            if prev.status == "ok":
                results[i] = prev
                continue
        todo.append(i)
    if len(results):
        print(f"  {len(results)} gereksinim önceki çalıştırmadan alındı (yeniden üretmek için --force)")

    stop = threading.Event()

    def work(i: int) -> tuple[int, GenerationResult | None]:
        if stop.is_set():  # a worker may pick the next item before the main thread cancels the queue
            return i, None
        r = targets[i]
        try:
            res = gen.generate(r, neighbors_of(r, reqs_all, n_neighbors), exclude_projects)
        except BaseException:
            stop.set()
            raise
        (items / f"{safe_name(r.id)}.json").write_text(json.dumps(asdict(res), ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
        return i, res

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, int(gen.cfg["llm"]["concurrency"]))) as pool:
        futures = [pool.submit(work, i) for i in todo]
        try:
            for fut in as_completed(futures):
                i, res = fut.result()
                if res is None:
                    continue
                done += 1
                results[i] = res
                info = (f"{len(res.test_cases)} test" + (f", {len(res.warnings)} uyarı" if res.warnings else "")
                        if res.status == "ok" else f"HATA: {res.error[:160]}")
                print(f"  [{done}/{len(todo)}] {res.requirement_id}: {info} ({res.seconds} sn)")
        except BaseException:  # fatal LLM error or Ctrl+C: do not start the queued requirements
            stop.set()
            for f in futures:
                f.cancel()
            raise
    return [results[i] for i in range(len(targets))]
