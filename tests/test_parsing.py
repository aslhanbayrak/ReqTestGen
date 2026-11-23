import copy
import unittest
from pathlib import Path

from reqtestgen.config import DEFAULTS, _merge
from reqtestgen.doors import (IdIndex, extract_requirements, extract_test_cases, level_from_number, load_module,
                              module_from_json, split_links)
from reqtestgen.html_import import module_from_html
from reqtestgen.library import load_project, load_projects

EX = Path(__file__).resolve().parent.parent / "examples"
SAMPLE_DXL = Path(__file__).resolve().parents[2] / "tools" / "doors" / "sample_export.json"


def cfg(**over):
    return _merge(copy.deepcopy(DEFAULTS), over)


class TestDoors(unittest.TestCase):
    def test_level_from_number(self):
        self.assertEqual(level_from_number("1.6.1.1"), 4)
        self.assertEqual(level_from_number("1.6.1.1.0-2"), 5)
        self.assertEqual(level_from_number("1.0-1"), 2)
        self.assertEqual(level_from_number(""), 0)

    def test_links_keep_spaces_in_ids(self):
        self.assertEqual(split_links("SMSW_IFF_MGR FRS_4920\nSMSW_IFF_MGR FRS_17090; X-1", cfg()),
                         ["SMSW_IFF_MGR FRS_4920", "SMSW_IFF_MGR FRS_17090", "X-1"])
        self.assertEqual(split_links("N/A", cfg()), [])

    def test_id_index_suffix_match(self):
        idx = IdIndex(["SMSW_FMU FRS_101", "SMSW_FMU FRS_1101"])
        self.assertEqual(idx.resolve("smsw_fmu  frs_101"), "SMSW_FMU FRS_101")
        self.assertEqual(idx.resolve("FRS_101"), "SMSW_FMU FRS_101")
        self.assertIsNone(idx.resolve("101X"))

    def test_avista_dxl_json(self):
        """The JSON written by tools/doors/ExportModule.dxl is read with AVISTA's classification."""
        if not SAMPLE_DXL.is_file():
            self.skipTest("sample_export.json not in this checkout")
        mod = load_module(SAMPLE_DXL, cfg())
        cases = extract_test_cases(mod, cfg())
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].title, "Mode Control")
        self.assertEqual([s.kind for s in cases[0].steps], ["precondition", "step"])
        self.assertEqual(cases[0].links, ["SMSW_IFF_MGR FRS_4920", "SMSW_IFF_MGR FRS_17090"])

    def test_json_without_hierarchy(self):
        mod = module_from_json([{"id": "R1", "Object Heading": "Mode"}, {"id": "R2", "Object Text": "shall x"}])
        self.assertEqual([o.level for o in mod.objects], [1, 2])
        self.assertEqual(mod.objects[1].parent_abs_no, mod.objects[0].abs_no)
        reqs = extract_requirements(mod, cfg())
        self.assertEqual([(r.id, r.section) for r in reqs], [("R2", ["Mode"])])


class TestHtml(unittest.TestCase):
    def test_table_with_named_columns(self):
        mod = load_module(EX / "proje_a" / "requirements.html", cfg())
        reqs = extract_requirements(mod, cfg())
        self.assertEqual([r.id for r in reqs], [f"SMSW_FMU FRS_10{i}" for i in range(1, 6)])
        self.assertEqual(reqs[0].section, ["3 FUNCTIONAL REQUIREMENTS", "3.1 Pump Control"])
        self.assertIn("\n", reqs[3].text)  # <br> kept as a line break

    def test_main_column_with_bold_headings(self):
        c = cfg(html={"columns": {"main": ["VCU Requirements"]}})
        reqs = extract_requirements(load_module(EX / "proje_b" / "srs.html", c), c)
        self.assertEqual(len(reqs), 5)
        self.assertEqual(reqs[0].section, ["2 Software Requirements", "2.4 Crossfeed Valve", "2.4.1 Valve Commands"])
        self.assertEqual(reqs[3].section[-1], "2.4.2 Valve Monitoring")

    def test_paragraphs_without_table(self):
        reqs = extract_requirements(load_module(EX / "proje_c" / "requirements.html", cfg()), cfg())
        self.assertEqual([r.id for r in reqs], ["HPU_SRS_001", "HPU_SRS_002", "HPU_SRS_003", "HPU_SRS_004"])
        self.assertIn("Shorter low-pressure", reqs[2].text)  # continuation paragraph appended
        self.assertEqual(reqs[2].section[-1], "4.2 Pressure Monitoring")

    def test_entities_and_nested_markup(self):
        html = ("<table><tr><th>ID</th><th>Object Text</th></tr>"
                "<tr><td>R&#45;1</td><td><p>a &amp; <i>b</i></p><ul><li>one</li><li>two</li></ul></td></tr></table>")
        mod = module_from_html(html, cfg())
        self.assertEqual(mod.objects[0].id, "R-1")
        self.assertEqual(mod.objects[0].object_text, "a & b\none\ntwo")

    def test_projects_and_links(self):
        pf = load_projects(EX / "projects.json")
        a = load_project(pf.get("ProjeA"), cfg())
        b = load_project(pf.get("projeb"), cfg())
        self.assertEqual(sorted(a.cases_by_req), [f"SMSW_FMU FRS_10{i}" for i in range(1, 5)])
        self.assertEqual(len(b.cases_by_req), 4)
        self.assertEqual(a.unmatched_links + b.unmatched_links, [])
        self.assertEqual(len(a.cases_by_req["SMSW_FMU FRS_104"][0].steps), 3)


if __name__ == "__main__":
    unittest.main()
