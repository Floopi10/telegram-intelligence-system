from __future__ import annotations

import json
import unittest

from offer_parser.digest import select_twitter_radar
from offer_parser.twitter_evidence import (
    compact_evidence,
    evidence_rank_bonus,
    extract_telegram_performance_evidence,
)


class TwitterEvidenceTests(unittest.TestCase):
    def test_extracts_x_family_outcome_without_inventing_metrics(self):
        result = extract_telegram_performance_evidence(
            "С поста лутанул больше 300 пдп: 90к просмотров, 3600 лайков за 24 часа. Видео."
        )
        self.assertEqual(result.claimed_views, 90_000)
        self.assertEqual(result.claimed_likes, 3_600)
        self.assertEqual(result.followers_gained, 300)
        self.assertEqual(result.result_hours, 24)
        self.assertEqual(result.content_format, "видео")
        self.assertAlmostEqual(result.follower_conversion or 0, 1 / 300)

    def test_missing_values_stay_missing(self):
        result = extract_telegram_performance_evidence("Полезный разбор нового AI-инструмента")
        self.assertFalse(result.has_outcome)
        self.assertIsNone(result.follower_conversion)
        self.assertEqual(evidence_rank_bonus(result.as_dict()), 0)
        self.assertEqual(compact_evidence(result.as_dict()), "")

    def test_cost_is_not_misread_as_revenue(self):
        result = extract_telegram_performance_evidence(
            "Two used RTX 3060s ($600) and a PC from 2014 built this agent"
        )
        self.assertIsNone(result.revenue_usd)

    def test_status_id_is_not_misread_as_likes(self):
        result = extract_telegram_performance_evidence(
            "https://x.com/user/status/2105269885727019144\nЛайк бук коммент"
        )
        self.assertIsNone(result.claimed_likes)

    def test_tiny_claimed_counts_are_not_rendered_as_evidence(self):
        result = extract_telegram_performance_evidence("5 likes и 70 views")
        self.assertEqual(compact_evidence(result.as_dict()), "")

    def test_closest_metric_pair_wins_in_long_case_study(self):
        result = extract_telegram_performance_evidence(
            "Пост собрал 200к просмотров и 10к лайков. "
            "При этом со 100к просмотров я получаю 400+ подписчиков."
        )
        self.assertEqual(result.claimed_views, 100_000)
        self.assertEqual(result.followers_gained, 400)
        self.assertAlmostEqual(result.follower_conversion or 0, 0.004)

    def test_self_reported_evidence_improves_order_but_not_quality_gate(self):
        def row(index: int, evidence: dict) -> dict:
            return {
                "canonical_url": f"https://x.com/a/status/{index}",
                "content_score": 8,
                "views": 800,
                "likes": 30,
                "reposts": 5,
                "bookmarks": 5,
                "views_per_hour": 200,
                "velocity_percentile": 0.90,
                "signal_percentile": 0.80,
                "signal_rate": 0.05,
                "strength_decision": "keep",
                "strength_kind": "подтверждённый победитель",
                "analysis_json": json.dumps({
                    "topic": "Claude AI workflow",
                    "hook": "Практический AI-кейс",
                    "telegram_evidence": evidence,
                }),
            }

        strong = extract_telegram_performance_evidence(
            "+300 подписчиков с 90к просмотров за 24 часа, статья"
        ).as_dict()
        selected = select_twitter_radar([row(1, {}), row(2, strong)], 10)
        self.assertEqual(selected[0]["canonical_url"], "https://x.com/a/status/2")

        weak = row(3, strong)
        weak.update({
            "views": 20,
            "likes": 0,
            "reposts": 0,
            "bookmarks": 0,
            "views_per_hour": 2,
            "velocity_percentile": None,
            "signal_percentile": None,
            "signal_rate": 0,
            "strength_decision": "watch",
            "strength_kind": "watch",
        })
        self.assertEqual(select_twitter_radar([weak], 10), [])


if __name__ == "__main__":
    unittest.main()
