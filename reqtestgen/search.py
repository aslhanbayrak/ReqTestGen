"""Text search used for example tests and system documents.

* lexical: BM25 (pure Python, no model needed - works when no embedding model is available)
* embedding: cosine similarity of vectors from the LLM server's /embeddings endpoint
* hybrid: both rankings fused with reciprocal rank fusion (RRF)

Embeddings are cached on disk by SHA-1 of (model, text) so re-runs cost nothing.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter
from pathlib import Path

from .llm import LLMClient, LLMError

_STOP = set("""a an and are as at be by for from has have if in into is it its of on or that the then this to
was were will with shall should must when while which who not no any all each other than such
ve ile bir bu şu o da de ki için gibi olarak olan veya ya ise ne mi""".split())


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens. Identifiers such as CC_IFF_OPER_MODE are kept whole AND split
    into parts, so both exact signal names and their words match."""
    t = (text or "").replace("İ", "i").replace("I", "i").lower()
    out: list[str] = []
    for tok in re.findall(r"\w+", t):
        if tok in _STOP:
            continue
        out.append(tok)
        if "_" in tok:
            out.extend(p for p in tok.split("_") if len(p) > 1 and p not in _STOP)
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df: Counter = Counter()
        for d in self.tf:
            df.update(d.keys())
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        q = Counter(query)
        out = []
        for tf, ln in zip(self.tf, self.len):
            s = 0.0
            for w in q:
                f = tf.get(w)
                if f:
                    s += self.idf[w] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / (self.avg or 1)))
            out.append(s)
        return out


def tfidf_vectors(texts: list[str]) -> list[dict]:
    toks = [tokenize(t) for t in texts]
    df: Counter = Counter()
    for t in toks:
        df.update(set(t))
    n = len(texts)
    vecs = []
    for t in toks:
        tf = Counter(t)
        v = {w: (1 + math.log(c)) * math.log(1 + n / df[w]) for w, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs


def cosine_sparse(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class EmbeddingCache:
    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        if path and path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                self.data = {}

    @staticmethod
    def key(model: str, text: str) -> str:
        return hashlib.sha1((model + "\n" + text).encode("utf-8")).hexdigest()

    def embed(self, llm: LLMClient, model: str, texts: list[str]) -> list[list[float]]:
        keys = [self.key(model, t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self.data]
        if missing:
            vecs = llm.embed([texts[i] for i in missing], model=model)
            with self._lock:
                for i, v in zip(missing, vecs):
                    self.data[keys[i]] = [round(x, 6) for x in v]
                self.save()
        return [self.data[k] for k in keys]

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), encoding="utf-8")


def resolve_mode(cfg: dict) -> str:
    mode = cfg["retrieval"].get("mode", "auto")
    if mode == "auto":
        return "hybrid" if cfg["llm"].get("embed_model") else "lexical"
    return mode


class SearchIndex:
    """Ranks a fixed list of texts against a query."""

    def __init__(self, texts: list[str], cfg: dict, llm: LLMClient | None = None,
                 cache: EmbeddingCache | None = None, label: str = ""):
        self.texts = texts
        self.cfg = cfg
        self.llm = llm
        self.cache = cache or EmbeddingCache(None)
        self.mode = resolve_mode(cfg)
        self.bm25 = BM25([tokenize(t) for t in texts])
        self.vectors: list[list[float]] | None = None
        if self.mode in ("embedding", "hybrid") and texts:
            if llm is None:
                self.mode = "lexical"
            else:
                try:
                    self.vectors = self.cache.embed(llm, cfg["llm"]["embed_model"], texts)
                except LLMError as e:
                    print(f"  Uyarı: {label} için embedding alınamadı ({e}); yalnızca kelime araması kullanılacak.")
                    self.mode = "lexical"

    def search(self, query: str, k: int, allowed=None) -> list[tuple[int, float]]:
        """[(index, score)] best first. `allowed(i)` filters candidates (e.g. other projects only).
        Scores are BM25 (lexical), cosine (embedding) or RRF (hybrid)."""
        idx = [i for i in range(len(self.texts)) if allowed is None or allowed(i)]
        if not idx:
            return []
        lex = self.bm25.scores(tokenize(query))
        if self.mode == "lexical" or self.vectors is None:
            ranked = sorted(idx, key=lambda i: -lex[i])
            return [(i, lex[i]) for i in ranked[:k] if lex[i] > 0]
        qv = self.cache.embed(self.llm, self.cfg["llm"]["embed_model"], [query])[0]
        dense = {i: cosine(qv, self.vectors[i]) for i in idx}
        if self.mode == "embedding":
            ranked = sorted(idx, key=lambda i: -dense[i])
            return [(i, dense[i]) for i in ranked[:k]]
        rrf: dict[int, float] = {}
        for ranking in (sorted(idx, key=lambda i: -lex[i]), sorted(idx, key=lambda i: -dense[i])):
            for r, i in enumerate(ranking):
                rrf[i] = rrf.get(i, 0.0) + 1.0 / (60 + r)
        ranked = sorted(idx, key=lambda i: -rrf[i])
        return [(i, rrf[i]) for i in ranked[:k]]
