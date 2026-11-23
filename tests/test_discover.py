import copy
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from reqtestgen.config import DEFAULTS
from reqtestgen.discover import scan, write_projects
from reqtestgen.library import load_project, load_projects
from reqtestgen.menu import pack, write_env

EX = Path(__file__).resolve().parent.parent / "examples"


class TestDiscover(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        data = self.tmp / "data"
        shutil.copytree(EX / "gmh_demo", data / "GMH", ignore=shutil.ignore_patterns("*.json"))
        (data / "PROJEB").mkdir()
        for f in ("srs.html", "stp.html"):
            shutil.copy(EX / "proje_b" / f, data / "PROJEB" / f)
        (data / "YENI").mkdir()
        shutil.copy(EX / "proje_c" / "requirements.html", data / "YENI" / "requirements.html")
        shutil.copy(EX / "proje_c" / "docs" / "hpu_icd.html", data / "YENI" / "hpu_icd.html")
        self.cfg = copy.deepcopy(DEFAULTS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_builds_projects(self):
        content = scan(self.tmp / "data", self.tmp, self.cfg, log=lambda *_: None)
        by = {p["name"]: p for p in content["projects"]}
        self.assertEqual(sorted(by), ["GMH", "PROJEB", "YENI"])
        self.assertEqual(by["GMH"]["requirements"], "data/GMH/CC_SSS_FLIR_SSS.htm")
        self.assertEqual(by["GMH"]["tests"], "data/GMH/aselflir-testdoc.htm")
        self.assertEqual(sorted(d["path"] for d in by["GMH"]["docs"]),
                         ["data/GMH/CC_MKSD_CC_MKSD.htm", "data/GMH/CC_SSDD_CC_SSDD.htm"])
        self.assertEqual(by["PROJEB"]["requirements"], "data/PROJEB/srs.html")
        self.assertNotIn("tests", by["YENI"])
        self.assertEqual(by["YENI"]["requirements"], "data/YENI/requirements.html")
        self.assertEqual([d["path"] for d in by["YENI"]["docs"]], ["data/YENI/hpu_icd.html"])

        write_projects(content, self.tmp / "projects.json")
        pf = load_projects(self.tmp / "projects.json")
        gmh = load_project(pf.get("GMH"), self.cfg)
        self.assertEqual(sorted(gmh.cases_by_req), ["FLIR-SSS-45", "FLIR-SSS-46"])
        self.assertEqual(len(load_project(pf.get("PROJEB"), self.cfg).cases_by_req), 4)
        # evaluation: no examples from the own program; a new project learns from all programs
        self.assertEqual([p.name for p in pf.example_projects(pf.get("GMH"))], ["PROJEB"])
        self.assertEqual(sorted(p.name for p in pf.example_projects(pf.get("YENI"))), ["GMH", "PROJEB"])

    def test_pack_and_env(self):
        z = pack(self.tmp / "p.zip")
        names = zipfile.ZipFile(z).namelist()
        self.assertIn("ReqTestGen/reqtestgen/cli.py", names)
        self.assertIn("ReqTestGen/baslat.bat", names)
        self.assertFalse(any(n.endswith("/.env") or "/runs/" in n or "__pycache__" in n for n in names))

        env = self.tmp / ".env"
        env.write_text("# comment\nLLM_BASE_URL=old\nOTHER=1\n", encoding="utf-8")
        write_env(env, {"LLM_BASE_URL": "http://x/v1", "LLM_CHAT_MODEL": "m"})
        self.assertEqual(env.read_text(encoding="utf-8"),
                         "# comment\nLLM_BASE_URL=http://x/v1\nOTHER=1\nLLM_CHAT_MODEL=m\n")


if __name__ == "__main__":
    unittest.main()
