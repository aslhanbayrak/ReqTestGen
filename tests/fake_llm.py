"""A tiny OpenAI-compatible server for tests (no network, no model).

Chat replies are derived from the TARGET REQUIREMENT in the prompt: "CC_*" signals named in the
requirement become inputs, the other signals become checked outputs, so evaluation metrics have
something real to compare. `server.script` can queue raw (status, body) replies to simulate
errors.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def fake_tests_for(prompt: str) -> dict:
    m = re.search(r"TARGET REQUIREMENT\nID: (.+)\n(?:.*\n)*?Text:\n(.+)", prompt)
    rid, text = (m.group(1).strip(), m.group(2)) if m else ("?", "")
    pairs = re.findall(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b (?:is|to|as|at) (\(\d+\) [A-Z_]+)", text)
    inputs = [f"{s} : {v}" for s, v in pairs if s.startswith("CC_") or s.endswith("_MODE") or "LOW_PRESSURE" in s
              or "PRESSURE_OK" in s]
    outputs = [f"{s} is displayed as {v}" for s, v in pairs if f"{s} : {v}" not in inputs]
    return {
        "requirement_id": rid,
        "analysis": "fake analysis",
        "test_cases": [{
            "title": f"Verify {rid}",
            "category": "normal",
            "preconditions": [],
            "steps": [{"object_text": "Using Simulator set the followings;\nCDU simulator;\n" + "\n".join(inputs),
                       "expected_results": "Verify that,\n" + "\n".join(outputs) if outputs else "N/A"}],
        }],
        "open_issues": [],
    }


def fake_vector(text: str, dim: int = 64) -> list[float]:
    v = [0.0] * dim
    for tok in re.findall(r"\w+", text.lower()):
        v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % dim] += 1.0
    return v


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status: int, body, headers=None):
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        srv = self.server
        if srv.require_key and self.headers.get("Authorization") != f"Bearer {srv.require_key}":
            return self._send(401, {"error": {"message": "invalid key", "code": "unauthorized"}})
        if self.path.endswith("/models"):
            return self._send(200, {"data": [{"id": "fake-chat"}, {"id": "fake-embed"}]})
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        srv = self.server
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with srv.lock:
            srv.requests.append((self.path, payload))
            scripted = srv.script.pop(0) if srv.script else None
        if srv.require_key and self.headers.get("Authorization") != f"Bearer {srv.require_key}":
            return self._send(401, {"error": {"message": "invalid key", "code": "unauthorized"}})
        if scripted:
            status, body, *hdr = scripted
            return self._send(status, body, hdr[0] if hdr else None)
        if self.path.endswith("/embeddings"):
            inp = payload["input"]
            return self._send(200, {"data": [{"index": i, "embedding": fake_vector(t)} for i, t in enumerate(inp)],
                                    "usage": {"prompt_tokens": 1, "total_tokens": 1}})
        if self.path.endswith("/chat/completions"):
            if srv.reject_json_mode and "response_format" in payload:
                return self._send(400, {"error": {"message": "response_format not supported"}})
            msgs = payload["messages"]
            system = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
            user = "\n".join(m["content"] for m in msgs if m["role"] == "user")
            if "You compare test cases" in system:
                n = len(re.findall(r"^\[\d+\]", user.split("GENERATED TEST CASES")[0], flags=re.M))
                reply = {"reference": [{"index": i + 1, "verdict": "covered", "matched_generated": [1], "reason": "ok"}
                                       for i in range(n)], "invalid_generated": [], "overall": "fine"}
            elif "TARGET REQUIREMENT" in user:
                reply = fake_tests_for(msgs[1]["content"])
            else:
                reply = {"ok": True}
            content = srv.wrap.replace("{}", json.dumps(reply))
            return self._send(200, {"choices": [{"message": {"role": "assistant", "content": content},
                                                 "finish_reason": "stop"}],
                                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        self._send(404, {"error": {"message": "not found"}})


class FakeLLM:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.lock = threading.Lock()
        self.httpd.requests = []
        self.httpd.script = []
        self.httpd.require_key = ""
        self.httpd.reject_json_mode = False
        self.httpd.wrap = "{}"          # e.g. "<think>hmm</think>```json\n{}\n```"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self.httpd

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    def start(self):
        self.thread.start()
        return self.httpd

    def stop(self):
        self.__exit__()
