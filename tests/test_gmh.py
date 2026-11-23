"""The layout seen in real GMH exports (synthetic content): main column named after the module,
object type in "Quality", two link columns in the SIT document, step-level links, bullet images,
tables inside cells, OLE figures, MKSD/SSDD objects linked to SSS requirements."""
import copy
import unittest
from pathlib import Path

from reqtestgen.config import DEFAULTS
from reqtestgen.doors import extract_requirements, load_module
from reqtestgen.evaluate import key_terms
from reqtestgen.generate import Generator
from reqtestgen.knowledge import KnowledgeBase, load_documents
from reqtestgen.library import load_project, load_projects
from reqtestgen.signals import Vocabulary

GMH = Path(__file__).resolve().parent.parent / "examples" / "gmh_demo"


def cfg():
    return copy.deepcopy(DEFAULTS)


class TestGmhLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pf = load_projects(GMH / "projects.json")
        cls.spec = cls.pf.get("GMH_FLIR_DEMO")
        cls.pcfg = cls.spec.config(cfg())
        cls.pd = load_project(cls.spec, cfg())

    def test_requirements(self):
        reqs = self.pd.requirements
        # headings, remark, figure, caption skipped; FLIR-SSS-47 filtered (Verification Method: Analysis)
        self.assertEqual([r.id for r in reqs], ["FLIR-SSS-45", "FLIR-SSS-46"])
        self.assertEqual(reqs[0].section[-1], "3.2 FLIR Page")
        self.assertIn("FLIR soft key is depressed", reqs[0].text)
        self.assertIn("CC-SSDD-273", reqs[0].links)
        self.assertIn("CC-SIT-FLIR-12", reqs[0].links)
        self.assertEqual(reqs[0].attributes["Rationale"], "In order to decrease the pilot workload.")

    def test_without_filter_analysis_requirement_is_kept(self):
        reqs = extract_requirements(load_module(GMH / "CC_SSS_FLIR_SSS.htm", cfg()), cfg())
        self.assertIn("FLIR-SSS-47", [r.id for r in reqs])

    def test_tests_use_the_resolving_link_column(self):
        pd = self.pd
        self.assertEqual(pd.link_column, "SIT Link(s)")  # "Links" is fuller but does not resolve
        self.assertEqual(sorted(pd.cases_by_req), ["FLIR-SSS-45", "FLIR-SSS-46"])
        case = pd.cases_by_req["FLIR-SSS-46"][0]
        self.assertEqual(case.title, "Aircraft (A/C) Data Window")
        self.assertEqual(case.path, ["1 FLIR Screen Objects Assignments"])
        # only the precondition and the step linked to this requirement
        self.assertEqual([s.id for s in case.steps], ["CC-SIT-FLIR-11", "CC-SIT-FLIR-13"])
        step = pd.cases_by_req["FLIR-SSS-45"][0].steps[1]
        self.assertEqual(step.expected_results,
                         "Verify that, On SMFD-2, FLIR page is observed.\n• The FLIR soft key label is observed as "
                         "Black on Cyan background.")
        self.assertEqual(len(pd.cases_by_req["FLIR-SSS-45"]), 1)  # requirement-side link to the same case

    def test_documents_and_trace_links(self):
        chunks, objects = load_documents(self.spec.docs, self.pcfg, self.pf.doc_roles)
        ids = {o.id for o in objects}
        self.assertIn("CC-MKSD-100", ids)
        self.assertNotIn("CC-MKSD-101", ids)  # OLE figure without text
        remark = next(o for o in objects if o.id == "CC-MKSD-90")
        self.assertIn("SK1 | DCLT1 | Black on Cyan", remark.text)  # table inside a cell
        self.assertTrue(all(c.role for c in chunks))
        kb = KnowledgeBase(chunks, self.pcfg, objects=objects)
        r45, r46 = self.pd.requirements
        self.assertEqual([o.id for o in kb.linked(r45.id, r45.links)], ["CC-SSDD-273"])
        # forward link (SSS Links -> SSDD) and back link (MKSD All Out Links -> SSS)
        self.assertEqual(sorted(o.id for o in kb.linked(r46.id, r46.links)), ["CC-MKSD-100", "CC-SSDD-274"])

    def test_test_module_among_documents_is_not_used(self):
        _, objects = load_documents([GMH / "aselflir-testdoc.htm"], self.pcfg)
        self.assertEqual(objects, [])

    def test_prompt_has_linked_objects_and_no_answer_leak(self):
        chunks, objects = load_documents(self.spec.docs, self.pcfg, self.pf.doc_roles)
        kb = KnowledgeBase(chunks, self.pcfg, objects=objects)
        gen = Generator(self.pcfg, None, None, Vocabulary(), kb)
        r46 = self.pd.requirements[1]
        messages, _, docs_used = gen.build_messages(r46, [])
        prompt = messages[1]["content"]
        self.assertIn("TRACE-LINKED DOCUMENT OBJECTS", prompt)
        self.assertIn("CC-MKSD-100", prompt)
        self.assertIn("MKSD - MFD/KDU ekran formatları", prompt)
        self.assertIn("Rationale: Declutter state shall be visible.", prompt)
        self.assertNotIn("Qualification Status", prompt)
        self.assertNotIn("CC-SIT-FLIR", prompt)
        self.assertTrue(any("(bağlantı)" in d for d in docs_used))

    def test_key_terms(self):
        terms = key_terms([{"steps": [{"object_text": 'On KDU-2, Depress LSK1 to set "HDG REF".',
                                       "expected_results": "Verify that, On VMFD-1, DCLT1 is observed as Black."}]}])
        self.assertTrue({"KDU-2", "LSK1", "HDG REF", "VMFD-1", "DCLT1"} <= terms)
        self.assertNotIn("ON", terms)


if __name__ == "__main__":
    unittest.main()
