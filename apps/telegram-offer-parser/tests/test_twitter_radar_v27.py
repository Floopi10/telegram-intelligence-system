from __future__ import annotations

import json
import unittest

from offer_parser.digest import _clean_hook, select_twitter_radar
from offer_parser.twitter_niches import classify_twitter_niche


class TwitterRadarTests(unittest.TestCase):
    def test_sport_is_blocked(self):
        self.assertIsNone(
            classify_twitter_niche(
                "Bayern attacking process with 3 xG and 20 shots per match"
            )
        )

    def test_expected_niches(self):
        self.assertEqual(classify_twitter_niche("humanoid robot demo"), "Роботы")
        self.assertEqual(classify_twitter_niche("Credit Suisse market settlement"), "Финансы")
        self.assertEqual(classify_twitter_niche("new Claude AI agent"), "AI")
        self.assertEqual(classify_twitter_niche("fashion and skincare creator"), "Девушки и lifestyle")

    def test_cross_niche_false_positives_are_not_misclassified(self):
        self.assertEqual(
            classify_twitter_niche("AI agents on Railway reduce sandbox costs"),
            "AI",
        )
        self.assertEqual(
            classify_twitter_niche("zero-day remote code execution in Windows"),
            "Технологии",
        )
        self.assertNotEqual(
            classify_twitter_niche("one model routes work between Claude and Grok"),
            "Девушки и lifestyle",
        )

    def test_unknown_topic_is_not_forced_into_marketing(self):
        self.assertIsNone(classify_twitter_niche("unexpected physical comedy moment"))

    def test_broken_mixed_language_hook_is_cleaned(self):
        value = _clean_hook(
            "Контрарныйstatement: сложный результат, seguido разоблачением"
        )
        self.assertEqual(
            value,
            "контрарное утверждение: сложный результат, после чего разоблачением",
        )

    def test_radar_keeps_good_and_drops_sport(self):
        rows = []
        for index in range(35):
            niche = "AI" if index % 2 == 0 else "Финансы"
            topic = "Claude AI agents" if niche == "AI" else "financial trading portfolio"
            rows.append({
                "canonical_url": f"https://x.com/a/status/{index}",
                "content_score": 8,
                "views": 1000 + index,
                "velocity_percentile": 0.8,
                "signal_percentile": 0.7,
                "signal_rate": 0.02,
                "views_per_hour": 500,
                "strength_decision": "keep",
                "strength_kind": "подтверждённый победитель",
                "analysis_json": json.dumps({
                    "ai_keep": True,
                    "niche": niche,
                    "topic": topic,
                    "hook": f"hook {index}",
                }),
            })
        rows.append({
            "canonical_url": "https://x.com/sport/status/1",
            "content_score": 10,
            "views": 1_000_000,
            "analysis_json": json.dumps({
                "ai_keep": True,
                "niche": "Спорт — отклонено",
                "hook": "Bayern match",
            }),
        })
        selected = select_twitter_radar(rows, 50)
        self.assertEqual(len(selected), 17)
        self.assertNotIn(
            "https://x.com/sport/status/1",
            {row["canonical_url"] for row in selected},
        )

    def test_low_view_llm_scores_do_not_fill_quota(self):
        rows = []
        for index in range(50):
            rows.append({
                "canonical_url": f"https://x.com/low/status/{index}",
                "content_score": 8,
                "views": 1 + index,
                "likes": 0,
                "reposts": 0,
                "bookmarks": 0,
                "views_per_hour": 2,
                "velocity_percentile": None,
                "signal_percentile": None,
                "signal_rate": 0,
                "strength_decision": "watch",
                "strength_kind": "watch",
                "analysis_json": json.dumps({"hook": "Claude AI agent workflow"}),
            })
        self.assertEqual(select_twitter_radar(rows, 50), [])

    def test_absolute_viral_and_relative_breakout_are_kept(self):
        rows = [
            {
                "canonical_url": "https://x.com/viral/status/1",
                "content_score": 7,
                "views": 1_400_000,
                "likes": 1000,
                "reposts": 200,
                "bookmarks": 100,
                "views_per_hour": 20_000,
                "velocity_percentile": None,
                "signal_percentile": None,
                "signal_rate": 0.002,
                "strength_decision": "watch",
                "strength_kind": "watch",
                "analysis_json": json.dumps({"hook": "Credit Suisse financial settlement"}),
            },
            {
                "canonical_url": "https://x.com/small/status/2",
                "content_score": 8,
                "views": 600,
                "likes": 30,
                "reposts": 5,
                "bookmarks": 8,
                "views_per_hour": 300,
                "velocity_percentile": 0.95,
                "signal_percentile": 0.80,
                "signal_rate": 0.08,
                "strength_decision": "keep",
                "strength_kind": "ранний кандидат",
                "analysis_json": json.dumps({"hook": "Claude AI agent workflow"}),
            },
        ]
        selected = select_twitter_radar(rows, 50)
        self.assertEqual(len(selected), 2)
        self.assertTrue(all(row["quality_tier"] == "winner" for row in selected))


if __name__ == "__main__":
    unittest.main()
