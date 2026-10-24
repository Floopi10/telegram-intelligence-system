import unittest

from offer_parser.knowledge_balance import evaluate_knowledge_balance


class KnowledgeContextTests(unittest.TestCase):
    def test_short_alpha_forward_is_allowed_as_trusted_source(self):
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
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "niche:AI")
        self.assertEqual(decision.primary_niche, "AI")
        self.assertIn("AI", decision.niches)
        self.assertNotIn("Web3", decision.niches)
        self.assertIn("Финансы", decision.niches)

    def test_alpha_link_without_context_is_still_rejected(self):
        decision = evaluate_knowledge_balance(
            text="https://x.com/example",
            chat_title="Alpha Professor",
            matcher_score=1,
            daily_total=0,
            daily_by_niche={},
            daily_limit=15,
        )
        self.assertFalse(decision.accepted)
        self.assertIn(decision.reason, {"no_relevant_niche", "link_only"})


if __name__ == "__main__":
    unittest.main()
