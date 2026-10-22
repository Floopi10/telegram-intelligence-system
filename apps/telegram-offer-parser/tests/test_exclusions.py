import csv
import tempfile
import unittest
from pathlib import Path

from offer_parser.chats import FIELDS, SelectedChat, write_chats
from offer_parser.exclusions import (
    exclude_by_names,
    filter_excluded,
    include_ids,
    normalize_title,
)


class KnowledgeExclusionsTest(unittest.TestCase):
    def test_normalize_ignores_case_spacing_and_invisible_marks(self):
        self.assertEqual(normalize_title("  Evey\u200b  Talent Hub  "), "evey talent hub")
        self.assertEqual(normalize_title("WOW Club ✨️"), normalize_title("wow club ✨"))

    def test_exclude_all_duplicate_titles_and_restore_one_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chats = root / "knowledge_chats.csv"
            exclusions = root / "knowledge_exclusions.csv"
            names = root / "names.txt"
            report = root / "report.md"
            write_chats(
                chats,
                [
                    {"enabled": "1", "chat_id": "-1001", "title": "Fluixo", "username": "a", "kind": "group"},
                    {"enabled": "1", "chat_id": "-1002", "title": "Fluixo", "username": "b", "kind": "group"},
                    {"enabled": "1", "chat_id": "-1003", "title": "Keep", "username": "c", "kind": "channel"},
                ],
            )
            names.write_text("fluixo\nFluixo\nMissing\n", encoding="utf-8")

            result = exclude_by_names(chats, exclusions, names, report)
            self.assertEqual(result.requested, 3)
            self.assertEqual(result.duplicate_names, 1)
            self.assertEqual(result.matched_names, 1)
            self.assertEqual(result.matched_chats, 2)
            self.assertEqual(result.not_found, ("Missing",))
            selected = filter_excluded(
                [SelectedChat(-1001, "Fluixo", "a"), SelectedChat(-1003, "Keep", "c")],
                exclusions,
            )
            self.assertEqual([chat.chat_id for chat in selected], [-1003])

            restored = include_ids(chats, exclusions, [-1001])
            self.assertEqual(restored, 1)
            with chats.open("r", encoding="utf-8-sig", newline="") as file:
                rows = {int(row["chat_id"]): row for row in csv.DictReader(file)}
            self.assertEqual(rows[-1001]["enabled"], "1")
            self.assertEqual(rows[-1002]["enabled"], "0")


if __name__ == "__main__":
    unittest.main()
