import copy
import time
import unittest
from pathlib import Path

from reqtestgen.config import DEFAULTS
from reqtestgen.llm import LLMClient, LLMError, extract_json, strip_reasoning
from reqtestgen.signals import Vocabulary, check_test_cases, parse_block, split_enum

from fake_llm import FakeLLM

EX = Path(__file__).resolve().parent.parent / "examples"


class TestSignals(unittest.TestCase):
    def test_parse_block_conventions(self):
        p = parse_block("Using Simulator set the followings;\nPVI_MGR simulator;\nCC_IFF_OPER_MODE : (2) NORMAL\n"
                        "Enable TX channel.\nWait 3 seconds\nIFF_CMD.COMMAND : ENABLE Inspect IFF LRM RX", "set")
        self.assertEqual([(l.message, l.signal, l.value, l.simulator) for l in p.lines],
                         [("", "CC_IFF_OPER_MODE", "(2) NORMAL", "PVI_MGR"), ("IFF_CMD", "COMMAND", "ENABLE", "PVI_MGR")])
        self.assertEqual(p.simulators, ["PVI_MGR", "IFF LRM"])
        q = parse_block("Verify that,\nIFF_X_TX is displayed as (1) ERROR", "check")
        self.assertEqual((q.lines[0].signal, q.lines[0].value), ("IFF_X_TX", "(1) ERROR"))
        self.assertEqual(split_enum("(2) NORMAL"), ("2", "NORMAL"))

    def test_model_xml_and_checks(self):
        v = Vocabulary()
        v.load_paths([EX / "models"])
        self.assertTrue(v.known("HPU_PUMP_STATUS"))
        self.assertEqual(v.signals["HPU_OPER_MODE"].enums, {"1": "STANDBY", "2": "NORMAL"})
        self.assertIn("HPU LRM", v.simulators)
        cases = [{"title": "t", "preconditions": [], "steps": [
            {"object_text": "CC_HYD_PUMP_CMD : (1) ON\nHPU_OPER_MODE : (3) NORMAL\nMADE_UP_SIG : 1",
             "expected_results": "Verify that,\nHPU_PUMP_STATUS is displayed as (1) STOPPED"},
            {"object_text": "TBD_FLOW : 1", "expected_results": "N/A"}]}]
        w = "\n".join(check_test_cases(cases, v))
        self.assertIn("MADE_UP_SIG", w)
        self.assertIn("HPU_OPER_MODE = (3)", w)
        self.assertIn("beklenen: (1) RUNNING", w)
        self.assertIn("TBD_FLOW", w)
        self.assertIn("Beklenen sonucu olmayan 1 adım", w)

    def test_relevant_prefers_named_signals(self):
        v = Vocabulary()
        v.load_paths([EX / "models"])
        names = [s.name for s in v.relevant("set HPU_PRESSURE_WARN when pump runs", 3)]
        self.assertEqual(names[0], "HPU_PRESSURE_WARN")


class TestLLM(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLLM()
        self.srv = self.fake.start()
        c = copy.deepcopy(DEFAULTS["llm"])
        c.update(base_url=self.fake.url, chat_model="fake-chat", embed_model="fake-embed", max_retries=3)
        self.llm = LLMClient(c)
        self.llm.backoff_base = 0.01

    def tearDown(self):
        self.fake.stop()

    def test_retry_then_success(self):
        self.srv.script = [(503, {"error": {"message": "busy"}}), (429, {"error": {"message": "slow"}}, {"Retry-After": "0"})]
        t = time.time()
        self.assertEqual(extract_json(self.llm.chat([{"role": "user", "content": "hi"}])), {"ok": True})
        self.assertLess(time.time() - t, 5)
        self.assertEqual(len(self.srv.requests), 3)

    def test_auth_error_is_fatal_with_server_message(self):
        self.srv.script = [(403, {"error": {"message": "Kullanım şartları kabul edilmedi", "code": "terms_not_accepted"}})]
        with self.assertRaises(LLMError) as cm:
            self.llm.chat([{"role": "user", "content": "hi"}])
        self.assertTrue(cm.exception.fatal)
        self.assertIn("terms_not_accepted", str(cm.exception))

    def test_json_mode_dropped_when_rejected(self):
        self.srv.reject_json_mode = True
        self.llm.chat([{"role": "user", "content": "hi"}])
        self.assertFalse(self.llm.json_mode_supported)
        self.assertNotIn("response_format", self.srv.requests[-1][1])

    def test_reasoning_and_fences_are_stripped(self):
        self.srv.wrap = "<think>long thoughts {not json}</think>Here:\n```json\n{}\n```"
        self.assertEqual(extract_json(self.llm.chat([{"role": "user", "content": "hi"}])), {"ok": True})
        self.assertEqual(strip_reasoning("abc</think> x"), "x")
        self.assertEqual(extract_json('{"a": [1, 2,],}'), {"a": [1, 2]})

    def test_embeddings_and_models(self):
        self.assertEqual(self.llm.list_models(), ["fake-chat", "fake-embed"])
        v = self.llm.embed(["a b", "c"])
        self.assertEqual(len(v), 2)
        self.assertAlmostEqual(sum(x * x for x in v[0]), 1.0, places=6)

    def test_unreachable_is_fatal(self):
        c = dict(self.llm.cfg, base_url="http://127.0.0.1:9/v1", max_retries=0)
        with self.assertRaises(LLMError) as cm:
            LLMClient(c).list_models()
        self.assertTrue(cm.exception.fatal)


if __name__ == "__main__":
    unittest.main()
