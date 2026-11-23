"""Compare generated tests with the reference tests engineers wrote for the same requirements.

Per requirement (only requirements that have reference tests):
* covered            - at least one test case was generated
* signal recall      - share of the signals used in the reference tests that the generated tests use
* signal precision   - share of generated signals that the reference tests also use
* check recall       - share of the reference "signal = value" verifications (Expected Results)
                       that also appear in the generated tests
* stimulus recall    - same for the "signal = value" inputs (Object Text)
* term recall/precision - same idea for key names (SMFD-3, LSK1, EGI, "HDG REF" ...): the metric that
                       works for cockpit/HMI style tests, where there are no "signal : value" lines
* case match recall  - share of reference test cases whose most similar generated test case is above
                       a similarity threshold (TF-IDF, or embeddings when an embedding model is set)
* judge (optional)   - an LLM compares behaviour: covered / partial / missing per reference test case,
                       plus generated test cases that test something not required ("invalid")

Signal metrics are exact and cheap; they say whether the right interfaces and values are exercised.
Similarity and judge scores say whether the same behaviour is tested. Read them together.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .doors import Requirement, TestCase, norm_id
from .generate import GenerationResult
from .llm import LLMClient, LLMError, extract_json
from .prompts import JUDGE_SYSTEM
from .search import EmbeddingCache, cosine, cosine_sparse, tfidf_vectors
from .signals import parse_step, split_enum


def _value_key(value: str) -> str:
    v, name = split_enum(value)
    return (v or name).upper().replace(" ", "")


def signal_sets(cases: list[dict]) -> tuple[set, set, set]:
    sigs, sets, checks = set(), set(), set()
    for c in cases:
        for st in c.get("preconditions", []) + c.get("steps", []):
            for ln in parse_step(st.get("object_text", ""), st.get("expected_results", "")).lines:
                name = ln.signal.upper()
                sigs.add(name)
                (sets if ln.kind == "set" else checks).add((name, _value_key(ln.value)))
    return sigs, sets, checks


_QUOTED = re.compile(r"[\"“”'‘’]([^\"“”'‘’\n]{2,40})[\"“”'‘’]")
_TERM = re.compile(r"(?<![A-Za-z0-9_])[A-Z][A-Z0-9]*(?:[-_/.][A-Z0-9]+)*(?![A-Za-z0-9_])")
_NOT_TERMS = {"THE", "AND", "OR", "NOT", "FOR", "WITH", "N/A", "NA", "TBD", "IS", "AS", "ON", "IN", "TO", "OF", "A", "I"}


def key_terms(cases: list[dict]) -> set[str]:
    """Names a tester would check: unit/display/key/page/label tokens (SMFD-3, LSK1, DCLT1, EGI, GPS1)
    and quoted names ("HDG REF"). Program-independent, works for signal and HMI style tests."""
    out: set[str] = set()
    for c in cases:
        for st in c.get("preconditions", []) + c.get("steps", []):
            text = f"{st.get('object_text', '')}\n{st.get('expected_results', '')}"
            out.update(re.sub(r"\s+", " ", q).strip().upper() for q in _QUOTED.findall(text))
            for t in _TERM.findall(text):
                if t not in _NOT_TERMS and (len(t) >= 3 or any(ch.isdigit() for ch in t)):
                    out.add(t)
    return out


def case_text(c: dict) -> str:
    rows = [c.get("title", "")]
    for st in c.get("preconditions", []) + c.get("steps", []):
        rows.append(st.get("object_text", ""))
        rows.append(st.get("expected_results", ""))
    return "\n".join(r for r in rows if r)


def _ratio(num: int, den: int):
    return round(num / den, 4) if den else None


@dataclass
class ReqEval:
    requirement_id: str
    ref_cases: int
    gen_cases: int
    covered: bool
    signal_recall: float | None = None
    signal_precision: float | None = None
    check_recall: float | None = None
    stimulus_recall: float | None = None
    term_recall: float | None = None
    term_precision: float | None = None
    missing_terms: list = field(default_factory=list)
    case_match_recall: float | None = None
    best_similarities: list = field(default_factory=list)
    missing_signals: list = field(default_factory=list)
    extra_signals: list = field(default_factory=list)
    judge: dict | None = None
    judge_recall: float | None = None
    judge_invalid: int | None = None
    status: str = "ok"


def _judge_one(llm: LLMClient, model: str, req: Requirement, refs: list[dict], gens: list[dict]) -> dict:
    def fmt(cases):
        return "\n\n".join(f"[{i}] {json.dumps(c, ensure_ascii=False)}" for i, c in enumerate(cases, 1))

    user = (f"REQUIREMENT {req.id}\n{req.text}\n\nREFERENCE TEST CASES\n{fmt(refs)}\n\n"
            f"GENERATED TEST CASES\n{fmt(gens)}\n\nReturn only the JSON object.")
    reply = llm.chat([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
                     model=model, temperature=0.0)
    return extract_json(reply)


def evaluate(requirements: list[Requirement], results: list[GenerationResult],
             reference: dict[str, list[TestCase]], cfg: dict, llm: LLMClient | None = None,
             judge: bool = False, cache: EmbeddingCache | None = None) -> dict:
    by_id = {norm_id(r.id): r for r in requirements}
    res_by_id = {norm_id(r.requirement_id): r for r in results}
    ref_by_id = {norm_id(k): v for k, v in reference.items()}
    targets = [rid for rid in res_by_id if rid in ref_by_id and rid in by_id]

    # similarity space shared by all requirements (better IDF than per requirement)
    texts, owners = [], []
    for rid in targets:
        for c in ref_by_id[rid]:
            texts.append(case_text(c.to_schema()))
            owners.append((rid, "ref"))
        for c in res_by_id[rid].test_cases:
            texts.append(case_text(c))
            owners.append((rid, "gen"))
    use_emb = bool(llm and cfg["llm"].get("embed_model"))
    vecs = None
    if use_emb and texts:
        try:
            vecs = (cache or EmbeddingCache(None)).embed(llm, cfg["llm"]["embed_model"], texts)
        except LLMError as e:
            print(f"  Uyarı: değerlendirmede embedding alınamadı ({e}); TF-IDF kullanılacak.")
    sparse = tfidf_vectors(texts) if vecs is None else None
    threshold = float(cfg["evaluation"]["match_threshold_embedding" if vecs is not None else "match_threshold_lexical"])

    def sim(a: int, b: int) -> float:
        return cosine(vecs[a], vecs[b]) if vecs is not None else cosine_sparse(sparse[a], sparse[b])

    rows: list[ReqEval] = []
    for rid in targets:
        refs = [c.to_schema() for c in ref_by_id[rid]]
        gen_res = res_by_id[rid]
        gens = gen_res.test_cases if gen_res.status == "ok" else []
        ev = ReqEval(by_id[rid].id, len(refs), len(gens), bool(gens), status=gen_res.status)
        r_sig, r_set, r_chk = signal_sets(refs)
        g_sig, g_set, g_chk = signal_sets(gens)
        ev.signal_recall = _ratio(len(r_sig & g_sig), len(r_sig))
        ev.signal_precision = _ratio(len(r_sig & g_sig), len(g_sig)) if r_sig else None
        ev.check_recall = _ratio(len(r_chk & g_chk), len(r_chk))
        ev.stimulus_recall = _ratio(len(r_set & g_set), len(r_set))
        ev.missing_signals = sorted(r_sig - g_sig)
        ev.extra_signals = sorted(g_sig - r_sig)
        r_terms, g_terms = key_terms(refs), key_terms(gens)
        ev.term_recall = _ratio(len(r_terms & g_terms), len(r_terms))
        ev.term_precision = _ratio(len(r_terms & g_terms), len(g_terms)) if r_terms else None
        ev.missing_terms = sorted(r_terms - g_terms)[:30]
        ri = [i for i, o in enumerate(owners) if o == (rid, "ref")]
        gi = [i for i, o in enumerate(owners) if o == (rid, "gen")]
        best = [max((sim(a, b) for b in gi), default=0.0) for a in ri]
        ev.best_similarities = [round(x, 3) for x in best]
        ev.case_match_recall = _ratio(sum(1 for x in best if x >= threshold), len(best))
        rows.append(ev)

    if judge and llm:
        model = cfg["llm"].get("judge_model") or cfg["llm"].get("chat_model")

        def run(ev: ReqEval):
            rid = norm_id(ev.requirement_id)
            gens = res_by_id[rid].test_cases
            if not gens:
                return ev, {"reference": [{"index": i + 1, "verdict": "missing"} for i in range(ev.ref_cases)]}
            try:
                return ev, _judge_one(llm, model, by_id[rid], [c.to_schema() for c in ref_by_id[rid]], gens)
            except LLMError as e:
                if e.fatal:
                    raise
                return ev, {"error": str(e)}
            except ValueError as e:
                return ev, {"error": f"hakem cevabı okunamadı: {e}"}

        with ThreadPoolExecutor(max_workers=max(1, int(cfg["llm"]["concurrency"]))) as pool:
            for ev, j in pool.map(run, rows):
                ev.judge = j
                verdicts = [str(x.get("verdict", "")).lower() for x in j.get("reference", []) if isinstance(x, dict)]
                if verdicts and ev.ref_cases:
                    score = sum(1.0 if v == "covered" else 0.5 if v == "partial" else 0.0 for v in verdicts[:ev.ref_cases])
                    ev.judge_recall = round(score / ev.ref_cases, 4)
                    ev.judge_invalid = len(j.get("invalid_generated") or [])

    def mean(attr):
        vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    summary = {
        "evaluated_requirements": len(rows),
        "coverage": _ratio(sum(1 for r in rows if r.covered), len(rows)),
        "reference_test_cases": sum(r.ref_cases for r in rows),
        "generated_test_cases": sum(r.gen_cases for r in rows),
        "signal_recall": mean("signal_recall"),
        "signal_precision": mean("signal_precision"),
        "check_recall": mean("check_recall"),
        "stimulus_recall": mean("stimulus_recall"),
        "term_recall": mean("term_recall"),
        "term_precision": mean("term_precision"),
        "case_match_recall": mean("case_match_recall"),
        "similarity": "embedding" if vecs is not None else "tfidf",
        "match_threshold": threshold,
        "judge_recall": mean("judge_recall"),
        "judge_invalid_cases": sum(r.judge_invalid or 0 for r in rows) if judge else None,
        "generated_without_reference": sorted(res_by_id[k].requirement_id for k in res_by_id if k not in ref_by_id),
        "reference_without_generation": sorted(by_id[k].id for k in ref_by_id if k in by_id and k not in res_by_id),
    }
    return {"summary": summary, "requirements": [r.__dict__ for r in rows]}
