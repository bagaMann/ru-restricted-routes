import unittest
from ru_routes.generate import parse_prefixes, render_bird


class PrefixTests(unittest.TestCase):
    def test_collapse_and_comments(self):
        prefixes = parse_prefixes('8.8.8.0/25\n8.8.8.128/25 # comment\n')
        self.assertEqual([str(p) for p in prefixes], ['8.8.8.0/24'])

    def test_private_rejected(self):
        with self.assertRaises(ValueError):
            parse_prefixes('10.0.0.0/8')

    def test_invalid_rejected(self):
        with self.assertRaises(ValueError):
            parse_prefixes('8.8.8.1/24')

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            parse_prefixes('# empty')

    def test_bird_output(self):
        self.assertIn('route 8.8.8.0/24 blackhole;', render_bird(parse_prefixes('8.8.8.0/24')))


if __name__ == '__main__':
    unittest.main()
