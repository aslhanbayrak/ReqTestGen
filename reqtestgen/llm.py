"""Minimal synchronous client for an OpenAI-compatible LLM server (standard library only).

Works with any server that speaks the OpenAI REST format: vLLM, Ollama, LiteLLM, LM Studio,
EVREN, OpenAI ... Only three endpoints are used: GET /models, POST /chat/completions and
(optionally) POST /embeddings.

Transient failures (network errors, 408/409/425/429/5xx) are retried with exponential backoff,
honouring a Retry-After header. Authentication errors are raised immediately with the server's
own message (e.g. EVREN's "terms_not_accepted").
"""
from __future__ import annotations

import json
import math
import random
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

TLS_HELP = ("LLM sunucusunun TLS sertifikası doğrulanamadı. Kurum içi bir sertifika kullanılıyorsa kök sertifikayı "
            "PEM dosyası olarak verin: config.json > llm.ca_bundle ya da LLM_CA_BUNDLE.")


class LLMError(Exception):
    """`fatal`: the configuration is wrong (address, key, TLS, model missing) - every further
    call would fail the same way, so a batch run stops instead of recording one error per item."""

    def __init__(self, status: int, message: str, fatal: bool = False):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.message = message
        self.fatal = fatal


def strip_reasoning(text: str) -> str:
    """Drop <think>...</think> blocks that reasoning models (DeepSeek-R1, Qwen3) put in content."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "</think>" in text:  # opening tag consumed by the chat template
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def extract_json(text: str) -> dict:
    """Parse the JSON object in an LLM reply (tolerates code fences and text around it)."""
    t = strip_reasoning(text)
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, flags=re.S)
    if fence and "{" in fence.group(1):
        t = fence.group(1)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("cevapta JSON nesnesi yok")
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        # common model slip: trailing commas
        return json.loads(re.sub(r",\s*([}\]])", r"\1", t[start:end + 1]))


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.strip()[:400]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        parts = [str(err.get("message") or ""), str(err.get("code") or err.get("type") or "")]
        return " | ".join(p for p in parts if p)[:400]
    if isinstance(err, str):
        return err[:400]
    return str(data.get("detail") or data.get("message") or body)[:400] if isinstance(data, dict) else body[:400]


def _retry_after(headers) -> float | None:
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _is_cert_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc)


class LLMClient:
    def __init__(self, llm_cfg: dict):
        self.cfg = llm_cfg
        self.base_url = (llm_cfg.get("base_url") or "").rstrip("/")
        self.api_key = llm_cfg.get("api_key") or ""
        self.json_mode_supported = bool(llm_cfg.get("json_mode", True))
        self.backoff_base = 2.0
        self.backoff_max = 60.0
        self._lock = threading.Lock()
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.calls = 0
        self._ctx = None
        if self.base_url.startswith("https://"):
            self._ctx = ssl.create_default_context()  # on Windows this includes the OS certificate store
            if llm_cfg.get("ca_bundle"):
                self._ctx.load_verify_locations(cafile=llm_cfg["ca_bundle"])

    # ---------------------------------------------------------------- core
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def request(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        if not self.base_url:
            raise LLMError(0, "LLM adresi tanımlı değil: .env içinde LLM_BASE_URL verin (ör. http://llm.sirket.local/v1)",
                           fatal=True)
        url = self.base_url + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        retries = int(self.cfg.get("max_retries", 5))
        timeout = timeout or float(self.cfg.get("timeout", 300))
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    self._count(body)
                    return body
            except urllib.error.HTTPError as e:
                msg = _error_message(e.read().decode("utf-8", "replace"))
                if e.code in (401, 403):
                    raise LLMError(e.code, "API anahtarı reddedildi veya yetki yok: " + msg, fatal=True) from None
                if e.code == 404:
                    raise LLMError(404, f"{path} bulunamadı. Base URL OpenAI uyumlu mu (genellikle /v1 ile biter)? "
                                        f"Model adı doğru mu? {msg}", fatal=True) from None
                if e.code not in RETRYABLE_STATUS or attempt > retries:
                    raise LLMError(e.code, msg) from None
                wait = _retry_after(e.headers)
                if wait is None:
                    wait = min(self.backoff_max, self.backoff_base * 2 ** (attempt - 1))
                last = f"HTTP {e.code}: {msg}"
            except (urllib.error.URLError, OSError) as e:
                if _is_cert_error(e):
                    raise LLMError(0, TLS_HELP + f" ({e})", fatal=True) from None
                if attempt > retries:
                    raise LLMError(0, f"LLM sunucusuna ulaşılamadı ({url}): {getattr(e, 'reason', e)}",
                                   fatal=True) from None
                wait = min(self.backoff_max, self.backoff_base * 2 ** (attempt - 1))
                last = f"{type(e).__name__}: {getattr(e, 'reason', e)}"
            wait *= 1 + random.uniform(0, 0.2)
            print(f"  LLM geçici hata ({last}); {wait:.0f} sn sonra tekrar denenecek ({attempt}/{retries})")
            time.sleep(wait)

    def _count(self, body: dict) -> None:
        usage = body.get("usage") if isinstance(body, dict) else None
        with self._lock:
            self.calls += 1
            if usage:
                self.tokens_prompt += int(usage.get("prompt_tokens") or 0)
                self.tokens_completion += int(usage.get("completion_tokens") or 0)

    def usage(self) -> dict:
        with self._lock:
            return {"calls": self.calls, "prompt_tokens": self.tokens_prompt,
                    "completion_tokens": self.tokens_completion,
                    "total_tokens": self.tokens_prompt + self.tokens_completion}

    # ----------------------------------------------------------- endpoints
    def list_models(self) -> list[str]:
        data = self.request("GET", "/models", timeout=30)
        return sorted(str(m.get("id")) for m in (data.get("data") or []) if isinstance(m, dict))

    def chat(self, messages: list[dict], *, model: str | None = None, json_mode: bool | None = None,
             temperature: float | None = None, max_tokens: int | None = None) -> str:
        model = model or self.cfg.get("chat_model")
        if not model:
            raise LLMError(0, "Sohbet modeli seçilmedi: config.json > llm.chat_model ya da LLM_CHAT_MODEL", fatal=True)
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": self.cfg.get("temperature", 0.2) if temperature is None else temperature,
            "max_tokens": int(max_tokens or self.cfg.get("max_tokens", 6000)),
        }
        payload.update(self.cfg.get("extra_body") or {})
        want_json = self.json_mode_supported if json_mode is None else (json_mode and self.json_mode_supported)
        if want_json:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = self.request("POST", "/chat/completions", payload)
        except LLMError as e:
            if not (want_json and e.status in (400, 422)):
                raise
            # some servers do not support response_format: the prompt already asks for JSON
            self.json_mode_supported = False
            payload.pop("response_format", None)
            data = self.request("POST", "/chat/completions", payload)
        choice = (data.get("choices") or [{}])[0]
        content = strip_reasoning((choice.get("message") or {}).get("content") or "")
        if not content and choice.get("finish_reason") == "length":
            raise LLMError(0, "Model cevabı max_tokens sınırında kesildi (düşünme çok uzun sürdü). "
                              "config.json > llm.max_tokens değerini artırın.")
        return content

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        model = model or self.cfg.get("embed_model")
        if not model:
            raise LLMError(0, "Embedding modeli tanımlı değil (llm.embed_model)", fatal=True)
        out: list[list[float]] = []
        batch = max(1, int(self.cfg.get("embed_batch", 16)))
        for i in range(0, len(texts), batch):
            data = self.request("POST", "/embeddings", {"model": model, "input": texts[i:i + batch]})
            rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
            for r in rows:
                v = [float(x) for x in r["embedding"]]
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                out.append([x / n for x in v])
        if len(out) != len(texts):
            raise LLMError(0, f"Embedding sayısı uyuşmuyor: {len(out)} / {len(texts)}")
        return out
