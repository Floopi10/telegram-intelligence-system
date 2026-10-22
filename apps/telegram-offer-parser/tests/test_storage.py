import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from offer_parser.storage import OfferRecord, Storage


class StorageTest(unittest.TestCase):
    def test_deduplicates_chat_and_message(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Storage(Path(temp_dir) / "offers.db")
            offer = OfferRecord(
                chat_id=-100123,
                message_id=42,
                chat_title="Test",
                sender_id=7,
                sender_name="Alice",
                message_date=datetime.now(timezone.utc),
                text="Paid collaboration",
                score=5,
                matched_rules="paid collaboration (+5)",
                compensation="",
                source_url=None,
            )
            self.assertTrue(storage.save(offer))
            self.assertFalse(storage.save(offer))
            self.assertEqual(storage.stats()[0], 1)
            storage.close()


if __name__ == "__main__":
    unittest.main()

