import unittest

from ru_routes.review import review


class ReviewTests(unittest.TestCase):
    def test_identical_candidate(self):
        baseline = "8.8.8.0/24\n1.1.1.0/24\n"
        result = review(baseline, baseline, 2, 2)
        self.assertTrue(result["approved"])
        self.assertEqual(result["added"], 0)

    def test_live_mismatch_rejected(self):
        baseline = "8.8.8.0/24\n1.1.1.0/24\n"
        result = review(baseline, baseline, 2, 1)
        self.assertFalse(result["approved"])

    def test_large_removal_rejected(self):
        old = "8.8.8.0/24\n1.1.1.0/24\n9.9.9.0/24\n"
        new = "8.8.8.0/24\n1.1.1.0/24\n"
        result = review(old, new, 3, 3)
        self.assertFalse(result["approved"])
        self.assertEqual(result["removed"], 1)

    def test_large_addition_rejected(self):
        old = "8.8.8.0/24\n1.1.1.0/24\n"
        new = old + "9.9.9.0/24\n"
        result = review(old, new, 2, 2)
        self.assertFalse(result["approved"])

    def test_small_change_allowed(self):
        old = "".join(f"8.8.{i * 2}.0/24\n" for i in range(20))
        new = old + "9.9.9.0/24\n"
        result = review(old, new, 20, 20)
        self.assertTrue(result["approved"])


if __name__ == "__main__":
    unittest.main()
