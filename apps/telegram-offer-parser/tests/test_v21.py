import tempfile
import unittest
from pathlib import Path

from offer_parser.digest_guard import split_digest_rows
from offer_parser.vacancy_filter import evaluate_vacancy, is_vacancy_text
from offer_parser.x_post_audit import render_audit


class VacancyGuardTest(unittest.TestCase):
    def test_accepts_only_profile_and_geography(self):
        accepted = "We are hiring a paid remote global Business Development Manager, salary $2500"
        self.assertTrue(is_vacancy_text(accepted))
        self.assertTrue(evaluate_vacancy(accepted).accepted)
        rejected = (
            "Hiring: Senior Product Manager in Dubai, AED 30000",
            "Hiring: Social Media Associate in Ghana, paid role",
            "Hiring: Growth Marketing Manager in Dubai, salary $5000",
            "Hiring: Partnership Manager LATAM Mexico Colombia Brazil, salary $3000",
            "Hiring: Enterprise Account Executive Remote USA, salary $150k",
        )
        for text in rejected:
            with self.subTest(text=text):
                self.assertFalse(evaluate_vacancy(text).accepted)

    def test_rejected_historical_vacancy_never_becomes_knowledge(self):
        rows = [
            {"text": "Hiring: Account Executive Remote USA salary $150k", "item_kind": "knowledge"},
            {"text": "Web3 retention breakdown", "item_kind": "knowledge"},
        ]
        vacancies, knowledge, rejected = split_digest_rows(rows)
        self.assertEqual(len(vacancies), 0)
        self.assertEqual(len(knowledge), 1)
        self.assertEqual(len(rejected), 1)


class FreeAuditTest(unittest.TestCase):
    def test_no_metrics_are_invented(self):
        result = render_audit("How I built an AI agent in 7 steps with a free prompt")
        self.assertIn("не зафиксированы", result)
        self.assertIn("private shares", result)

    def test_sanitized_metrics_are_used(self):
        result = render_audit("A useful breakdown", {"views": 1000, "likes": 20})
        self.assertIn("views: 1000", result)
        self.assertIn("likes: 20", result)


if __name__ == "__main__":
    unittest.main()
