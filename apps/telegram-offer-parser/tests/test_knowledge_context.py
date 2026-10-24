import unittest

from offer_parser.knowledge_balance import evaluate_knowledge_balance


class KnowledgeContextTests(unittest.TestCase):
    def test_short_alpha_forward_is_not_padded_by_x_url(self):
        decision = evaluate_knowledge_balance(
            text=(
                "Трейдинг платформа на SOL\n\n"
                "https://x.com/listendotmoney\n\n"
                "Аля агент"
            ),
            chat_title="Alpha Professor",
            matcher_score=1,
            daily_total=0,
            daily_by_niche={},
            daily_limit=15,
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "too_short")
        self.assertIn("AI", decision.niches)
        self.assertIn("Web3", decision.niches)
        self.assertIn("Финансы", decision.niches)


if __name__ == "__main__":
    unittest.main()
