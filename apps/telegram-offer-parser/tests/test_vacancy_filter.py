import unittest

from offer_parser.vacancy_filter import evaluate_vacancy


class VacancyFilterTest(unittest.TestCase):
    def assertAccepted(self, text):
        result = evaluate_vacancy(text)
        self.assertTrue(result.accepted, result.reason)

    def assertRejected(self, text, reason):
        result = evaluate_vacancy(text)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, reason)

    def test_accepts_paid_remote_global_bd(self):
        self.assertAccepted("Paid remote global Business Development Manager. Salary $2500/month.")

    def test_accepts_paid_remote_outreach(self):
        self.assertAccepted("Remote Outreach Specialist, paid monthly rate 1500 USDT.")

    def test_accepts_paid_russia_cis_community(self):
        self.assertAccepted("Вакансия: комьюнити-менеджер, удаленно, СНГ. Оплата 1200 USDT.")

    def test_accepts_related_growth_and_kol_roles(self):
        self.assertAccepted("Global remote Growth Marketer. Paid role, salary discussed.")
        self.assertAccepted("Worldwide KOL Manager vacancy. Monthly retainer $2000.")

    def test_rejects_us_only(self):
        self.assertRejected("Paid remote USA Business Development Manager, $3000.", "blocked_region")

    def test_rejects_latam_and_named_countries(self):
        for region in ("LATAM", "Mexico", "Colombia", "Brazil"):
            with self.subTest(region=region):
                self.assertRejected(f"Paid remote {region} Outreach Specialist, $2000.", "blocked_region")

    def test_rejects_media_manager(self):
        self.assertRejected("Paid global remote Media Manager, salary $2000.", "excluded_role")

    def test_rejects_unpaid_and_commission_only(self):
        self.assertRejected("Global remote BD Manager, unpaid internship.", "unpaid_or_commission_only")
        self.assertRejected("Remote Outreach Manager, commission-only.", "unpaid_or_commission_only")

    def test_rejects_payment_not_confirmed(self):
        self.assertRejected("Remote global Business Development Manager.", "payment_not_confirmed")

    def test_rejects_missing_remote_or_allowed_region(self):
        self.assertRejected("Paid Business Development Manager, salary $2000.", "location_not_remote_or_ru_cis")


if __name__ == "__main__":
    unittest.main()
