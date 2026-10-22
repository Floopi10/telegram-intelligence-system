from __future__ import annotations

import json
import unittest

from offer_parser.digest import select_twitter_radar
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

    def test_radar_keeps_good_and_drops_sport(self):
        rows = []
        for index in range(35):
            niche = "AI" if index % 2 == 0 else "Финансы"
            rows.append({
                "canonical_url": f"https://x.com/a/status/{index}",
                "content_score": 8,
                "views": 1000 + index,
                "velocity_percentile": 0.8,
                "signal_percentile": 0.7,
                "signal_rate": 0.02,
                "analysis_json": json.dumps({
                    "ai_keep": True,
                    "niche": niche,
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
        self.assertEqual(len(selected), 35)
        self.assertNotIn(
            "https://x.com/sport/status/1",
            {row["canonical_url"] for row in selected},
        )


if __name__ == "__main__":
    unittest.main()
