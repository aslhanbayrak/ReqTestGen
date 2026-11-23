"""End to end through the command line, against the fake LLM server."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reqtestgen.cli import main

from fake_llm import FakeLLM

EX = Path(__file__).resolve().parent.parent / "examples"


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(EX, self.tmp / "examples")
        self.fake = FakeLLM()
        self.srv = self.fake.start()
        self.srv.require_key = "secret"
        self.write_config(embed=False)

    def tearDown(self):
        self.fake.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_config(self, embed: bool):
        llm = {"chat_model": "fake-chat", "max_retries": 1}
        if embed:
            llm["embed_model"] = "fake-embed"
        (self.tmp / "config.json").write_text(json.dumps({"llm": llm}), encoding="utf-8")
        (self.tmp / ".env").write_text(f"LLM_BASE_URL={self.fake.url}\nLLM_API_KEY=secret\n", encoding="utf-8")

    def run_cli(self, *args) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(["--config", str(self.tmp / "config.json"), *args])
        return code, out.getvalue()

    def chat_calls(self):
        return sum(1 for p, _ in self.srv.requests if p.endswith("/chat/completions"))

    def test_crossval_lexical(self):
        out = self.tmp / "cv"
        code, log = self.run_cli("crossval", "--projects", str(self.tmp / "examples/projects.json"),
                                 "--out", str(out), "--cache", str(self.tmp / "cache"))
        self.assertEqual(code, 0, log)
        rows = json.loads((out / "crossval.json").read_text(encoding="utf-8"))
        self.assertEqual([r["project"] for r in rows], ["ProjeA", "ProjeB"])
        for r in rows:
            s = r["summary"]
            self.assertEqual(s["evaluated_requirements"], 4)
            self.assertEqual(s["coverage"], 1.0)
            self.assertGreater(s["signal_recall"], 0.3)
        for name in ("ProjeA", "ProjeB"):
            for f in ("tests.html", "evaluation.html", "results.json"):
                self.assertTrue((out / name / f).is_file(), f)
        # leave-one-project-out: ProjeA's prompts only show ProjeB examples
        res = json.loads((out / "ProjeA" / "results.json").read_text(encoding="utf-8"))["results"]
        self.assertTrue(all(e["project"] == "ProjeB" for r in res for e in r["examples"]))
        self.assertTrue(all(r["examples"] for r in res))
        self.assertIn("crossval.html", log)

    def test_generate_new_project_with_docs_embeddings_and_resume(self):
        self.write_config(embed=True)
        out = self.tmp / "run"
        args = ["generate", "--projects", str(self.tmp / "examples/projects.json"), "--target", "ProjeC",
                "--out", str(out), "--cache", str(self.tmp / "cache")]
        code, log = self.run_cli(*args)
        self.assertEqual(code, 0, log)
        res = json.loads((out / "results.json").read_text(encoding="utf-8"))["results"]
        self.assertEqual([r["requirement_id"] for r in res], ["HPU_SRS_001", "HPU_SRS_002", "HPU_SRS_003", "HPU_SRS_004"])
        self.assertTrue(all(r["status"] == "ok" and r["test_cases"] for r in res))
        self.assertTrue(any(r["docs"] for r in res), "system documents should be retrieved")
        prompt = next(p["messages"][1]["content"] for path, p in self.srv.requests if path.endswith("/chat/completions"))
        self.assertIn("SYSTEM DOCUMENT EXCERPTS", prompt)
        self.assertIn("KNOWN SIGNALS", prompt)
        self.assertIn("HPU_PUMP_STATUS", prompt)
        html = (out / "tests.html").read_text(encoding="utf-8")
        self.assertIn("HPU_SRS_003", html)
        self.assertFalse((out / "evaluation.html").exists())  # no reference tests for a new project

        before = self.chat_calls()
        code, log = self.run_cli(*args)
        self.assertEqual(code, 0, log)
        self.assertEqual(self.chat_calls(), before, "second run must reuse saved results")
        self.assertIn("önceki çalıştırmadan", log)

    def test_ablation_flags(self):
        out = self.tmp / "abl"
        code, log = self.run_cli("generate", "--projects", str(self.tmp / "examples/projects.json"), "--target", "ProjeA",
                                 "--out", str(out), "--no-examples", "--no-signals", "--limit", "2", "--judge")
        self.assertEqual(code, 0, log)
        prompt = next(p["messages"][1]["content"] for path, p in self.srv.requests if path.endswith("/chat/completions"))
        self.assertIn("EXAMPLES: none available", prompt)
        self.assertNotIn("KNOWN SIGNALS", prompt)
        ev = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
        self.assertEqual(ev["summary"]["evaluated_requirements"], 2)
        self.assertEqual(ev["summary"]["judge_recall"], 1.0)

    def test_bad_reply_is_repaired_and_fatal_error_stops(self):
        out = self.tmp / "bad"
        self.srv.script = [(200, {"choices": [{"message": {"content": "sorry, no json"}, "finish_reason": "stop"}]})]
        code, log = self.run_cli("generate", "--projects", str(self.tmp / "examples/projects.json"), "--target",
                                 "ProjeC", "--out", str(out), "--only", "HPU_SRS_001", "--no-eval")
        self.assertEqual(code, 0, log)
        res = json.loads((out / "results.json").read_text(encoding="utf-8"))["results"]
        self.assertEqual(res[0]["status"], "ok")  # second (repair) call succeeded

        (self.tmp / ".env").write_text(f"LLM_BASE_URL={self.fake.url}\nLLM_API_KEY=wrong\n", encoding="utf-8")
        code, log = self.run_cli("generate", "--projects", str(self.tmp / "examples/projects.json"), "--target",
                                 "ProjeC", "--out", str(self.tmp / "bad2"))
        self.assertEqual(code, 2)
        self.assertIn("API anahtarı reddedildi", log)
        self.assertLessEqual(self.chat_calls(), 2 + 2)  # stopped instead of failing every requirement

    def test_inspect_and_check(self):
        code, log = self.run_cli("inspect", str(self.tmp / "examples/proje_b/stp.html"))
        self.assertEqual(code, 0, log)
        self.assertIn("Dosya türü (otomatik): test", log)
        code, log = self.run_cli("check")
        self.assertEqual(code, 0, log)
        self.assertIn("fake-chat (listede var)", log)


if __name__ == "__main__":
    unittest.main()
