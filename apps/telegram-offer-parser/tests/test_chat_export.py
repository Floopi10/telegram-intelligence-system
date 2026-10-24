import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "export_chat_history.py"
SPEC = importlib.util.spec_from_file_location("export_chat_history", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ChatExportTests(unittest.TestCase):
    def test_safe_name_removes_path_characters(self):
        self.assertEqual(MODULE.safe_name('X/FAMILY:*?'), "X_FAMILY___")

    def test_supergroup_message_link(self):
        self.assertEqual(
            MODULE.message_link(-1004263161560, 10309),
            "https://t.me/c/4263161560/10309",
        )

    def test_non_supergroup_has_no_internal_link(self):
        self.assertIsNone(MODULE.message_link(-1234, 1))


if __name__ == "__main__":
    unittest.main()
