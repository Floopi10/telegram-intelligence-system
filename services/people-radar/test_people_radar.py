import sqlite3
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

if "offer_parser.config" not in sys.modules:
    package = types.ModuleType("offer_parser")
    config = types.ModuleType("offer_parser.config")
    config.Settings = object
    sys.modules.setdefault("offer_parser", package)
    sys.modules["offer_parser.config"] = config

from people_radar import detect_roles, is_kol_only, write_xlsx


class PeopleRadarTests(unittest.TestCase):
    def test_roles(self):
        roles = detect_roles("Co-founder and Business Development Manager at a Web3 startup")
        self.assertIn("Founder", roles)
        self.assertIn("BD/BDM", roles)

    def test_kol_only_is_excluded_but_kol_bd_is_not(self):
        self.assertTrue(is_kol_only("KOL and influencer", []))
        self.assertFalse(is_kol_only("KOL Business Development Manager", ["BD/BDM"]))

    def test_xlsx_is_valid_zip_with_two_sheets(self):
        row = {
            "score": 8, "name": "Alice", "username": "@alice",
            "telegram": "https://t.me/alice", "roles": "Founder", "bio": "Founder",
            "links": "https://example.com", "x": "https://x.com/alice",
            "sources": "Chat", "evidence": "I founded a startup",
            "evidence_link": "https://t.me/c/1/2", "last_seen": "2026-10-22", "user_id": 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "people.xlsx"
            write_xlsx(path, [("Все люди", [row]), ("Founders", [row])])
            self.assertTrue(zipfile.is_zipfile(path))
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                self.assertIn("xl/worksheets/sheet1.xml", names)
                self.assertIn("xl/worksheets/sheet2.xml", names)
                self.assertIn("Alice", archive.read("xl/worksheets/sheet1.xml").decode())


if __name__ == "__main__":
    unittest.main()
