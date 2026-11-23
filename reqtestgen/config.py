"""Settings.

Defaults live in DEFAULTS. A JSON file (config.json, non-secret settings) is deep-merged on
top, then the LLM_* environment variables (or a .env file next to it) override the provider
fields. The API key is only ever read from the environment / .env, never from config.json.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "llm": {
        "base_url": "",              # OpenAI-compatible, including the version path, e.g. http://llm.local/v1
        "api_key": "",               # from LLM_API_KEY only
        "chat_model": "",            # test writer, e.g. qwen3.8-flash-next / deepseek-v4.1-flash
        "embed_model": "",           # optional; empty = lexical (BM25) example search only
        "judge_model": "",           # optional; evaluation judge (empty = chat_model)
        "timeout": 300,
        "max_retries": 5,
        "concurrency": 2,            # parallel requirement generations
        "temperature": 0.2,
        "max_tokens": 6000,          # includes reasoning tokens on thinking models
        "json_mode": True,           # send response_format=json_object (dropped automatically if rejected)
        "extra_body": {},            # merged into every chat request, e.g. {"chat_template_kwargs": {"enable_thinking": false}}
        "ca_bundle": "",             # extra PEM file for an internal CA (the OS store is used by default)
        "embed_batch": 16,
    },
    "doors": {
        # Object Type values written into generated test modules (same as the existing test modules).
        "types": {
            "heading": "Heading",
            "remark": "Remark",
            "procedure": "Test Procedure",
            "part": "Test Part",
            "precondition": "Test Precondition",
            "step": "Test Step",
        },
        # Classification of test-module objects: case-insensitive substring match, checked in
        # this order: procedure, precondition, step, part, ignore (same rules as AVISTA DoorsImporter).
        "procedure_types": ["procedure"],
        "precondition_types": ["precondition", "pre-condition", "pre condition"],
        "step_types": ["step"],
        "part_types": ["part", "case", "group", "section"],
        "ignore_types": ["heading", "remark", "prerequisite", "information", "note", "figure", "table", "caption"],
        # Requirement modules: if requirement_types is empty, every object with text that is not
        # one of requirement_ignore_types counts as a requirement.
        "requirement_types": [],
        "requirement_ignore_types": ["heading", "information", "note", "remark", "comment", "title", "figure",
                                     "table", "caption", "ole"],
        # Only requirements whose attributes match, e.g.
        # {"Verification Method": ["Test"], "Configuration": ["Common", "w FLIR"]}
        # (attribute name part -> allowed value parts, case-insensitive; a missing attribute does not filter).
        "requirement_filter": {},
        # Separators between several links in one "FRS Link(s)" value. Spaces are NOT separators:
        # DOORS identifiers may contain one ("SMSW_IFF_MGR FRS_4920").
        "link_separators": "[\\r\\n,;|]+",
    },
    "html": {
        # Explicit column names (a name or a list of names), e.g.
        # {"id": "Object Identifier", "main": ["FLIR SSS", "CC SIT FLIR Procedure"], "frs_links": "SIT Link(s)"}.
        # Empty = detected: aliases below, then content (type values, longest text column, link columns).
        "columns": {},
        "column_aliases": {
            "id": ["id", "object identifier", "identifier", "object id", "req id", "requirement id", "test id"],
            "abs_no": ["absolute number", "abs no", "absno", "abs. no"],
            "number": ["object number", "number", "no", "paragraph", "para"],
            "level": ["object level", "level"],
            "object_type": ["object type", "type", "objecttype", "object_type", "quality", "object category"],
            "heading": ["object heading", "heading"],
            "object_text": ["object text", "text", "requirement text", "description"],
            "main": ["object heading and text", "main", "main column", "heading and text"],
            "expected_results": ["expected results", "expected result", "expected"],
        },
        # Headers matching this are link columns ("SIT Link(s)", "Out-links (...)", "All Out Links",
        # "Links", "SPS Link ID", "Verifies"). The fullest one becomes frs_links unless columns.frs_links
        # is given; tests then try every link column and keep the one that resolves to most requirements.
        "link_header_pattern": "link|verifies|traces?",
        # Used only for HTML without a usable table: a paragraph starting with an ID starts a requirement.
        "id_pattern": r"^\s*\[?([A-Za-z][A-Za-z0-9]*(?:[_ -][A-Za-z0-9]+)*[_-]\d+)\]?\s*[:.)\-]?\s+",
    },
    "retrieval": {
        "mode": "auto",              # auto | lexical | embedding | hybrid (auto = hybrid if embed_model else lexical)
        "k": 3,                      # example requirement/test pairs per prompt
        "max_example_chars": 9000,   # prompt budget for examples
        "max_cases_per_example": 4,
        "neighbor_requirements": 2,  # requirements before/after the target in the same section, as context
    },
    "knowledge": {
        "k": 4,                      # system-document chunks found by similarity, per prompt
        "chunk_chars": 1800,
        "max_chars": 9000,           # prompt budget for system-document text (linked + similar)
        # DOORS-module documents (SSDD, MKSD, ICD ... exported as HTML tables): objects linked to the
        # target requirement (its link columns, or their links back to it) are added first, up to
        # link_depth hops (2 = also the objects linked to those).
        "follow_links": True,
        "link_depth": 1,
        "max_linked": 8,
    },
    "generation": {
        # Requirement attributes shown in the prompt (attribute name parts, case-insensitive).
        # Empty list = all attributes.
        "prompt_attributes": ["rationale", "verification method", "verification level", "configuration",
                              "note", "expected", "source"],
        "id_prefix": "GEN",          # IDs of generated objects (DOORS assigns real ones on import)
        "max_signals": 60,           # known signals listed in a prompt
        "fix_unknown_signals": True, # one extra call when the model used signals missing from the vocabulary
        "context_file": "",          # optional text file: system description, glossary, conventions
    },
    "evaluation": {
        "match_threshold_lexical": 0.35,
        "match_threshold_embedding": 0.80,
    },
}

ENV_MAP = {
    "LLM_BASE_URL": ("llm", "base_url"),
    "LLM_API_KEY": ("llm", "api_key"),
    "LLM_CHAT_MODEL": ("llm", "chat_model"),
    "LLM_EMBED_MODEL": ("llm", "embed_model"),
    "LLM_JUDGE_MODEL": ("llm", "judge_model"),
    "LLM_CA_BUNDLE": ("llm", "ca_bundle"),
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def read_env_file(path: Path) -> dict:
    values: dict = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key.strip()] = val
    return values


def load_config(config_path: str | None = None, env_path: str | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(config_path) if config_path else Path("config.json")
    if path.is_file():
        user = json.loads(path.read_text(encoding="utf-8-sig"))
        user.get("llm", {}).pop("api_key", None)
        cfg = _merge(cfg, user)
    elif config_path:
        raise FileNotFoundError(f"Yapılandırma dosyası bulunamadı: {config_path}")

    env = read_env_file(Path(env_path) if env_path else path.parent / ".env")
    if not env and not env_path:
        env = read_env_file(Path(".env"))
    env.update({k: v for k, v in os.environ.items() if k in ENV_MAP})
    for name, (section, key) in ENV_MAP.items():
        if env.get(name):
            cfg[section][key] = env[name]
    cfg["llm"]["base_url"] = cfg["llm"]["base_url"].strip().rstrip("/")
    return cfg
