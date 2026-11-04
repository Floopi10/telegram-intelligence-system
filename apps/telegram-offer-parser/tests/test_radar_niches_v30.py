from __future__ import annotations

import unittest

from offer_parser.digest import NICHE_LIMITS
from offer_parser.twitter_niches import NICHE_ORDER, classify_twitter_niche, is_twitter_radar_noise


class NewRadarNichesTest(unittest.TestCase):
    def test_every_niche_has_a_digest_limit(self):
        self.assertEqual(set(NICHE_ORDER), set(NICHE_LIMITS))

    def test_true_crime(self):
        self.assertEqual(classify_twitter_niche("This serial killer hid 40 bodies for years"), "Тру-крайм")
        self.assertEqual(classify_twitter_niche("история маньяка, которого искали 20 лет"), "Тру-крайм")

    def test_killer_feature_is_not_true_crime(self):
        self.assertNotEqual(classify_twitter_niche("Claude killer feature for agents"), "Тру-крайм")
        self.assertIsNone(classify_twitter_niche("killer app for founders"))

    def test_adult_is_its_own_niche(self):
        self.assertEqual(classify_twitter_niche("my onlyfans model made 30k this month"), "18+")
        self.assertEqual(classify_twitter_niche("Seedance prompt for a girl outfit"), "Девушки и lifestyle")

    def test_streams(self):
        self.assertEqual(classify_twitter_niche("top twitch streamers clip farm"), "Стримы")
        self.assertEqual(classify_twitter_niche("https://x.com/ColeHayes28/status/1"), "Стримы")

    def test_casino_and_poker_stories(self):
        self.assertEqual(classify_twitter_niche("he lost everything at the casino in one night"), "Казино и ставки")
        self.assertEqual(classify_twitter_niche("проиграл 2 млн в покер за вечер"), "Казино и ставки")

    def test_casino_story_is_not_noise_but_sports_slips_are(self):
        self.assertFalse(is_twitter_radar_noise("poker drama: betting the house on one hand"))
        self.assertTrue(is_twitter_radar_noise("today's parlay and bet slip"))

    def test_physics_goes_to_math(self):
        self.assertEqual(classify_twitter_niche("физика для школьников за 5 минут"), "Математика")

    def test_beamnxw_is_an_ai_account(self):
        self.assertEqual(classify_twitter_niche("https://x.com/beamnxw/status/1"), "AI")


if __name__ == "__main__":
    unittest.main()
