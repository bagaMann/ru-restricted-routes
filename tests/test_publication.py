"""Offline tests for interrupted candidate publication."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ru_routes.generate import parse_prefixes, render_bird
from ru_routes.review import validate_publication


class PublicationTests(unittest.TestCase):
    def test_complete_and_interrupted_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            text = "8.8.8.0/24\n9.9.9.0/24\n"
            bird = render_bird(parse_prefixes(text))
            (state / "candidate.txt").write_text(text, encoding="utf-8")
            (state / "candidate.bird.conf").write_text(bird, encoding="utf-8")
            manifest = {
                "status": "candidate_not_applied",
                "prefix_count": 2,
                "candidate_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "bird_sha256": hashlib.sha256(bird.encode()).hexdigest(),
            }
            (state / "candidate.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(validate_publication(state, text), text)

            # Simulate crash after replacing candidate.txt but before BIRD/manifest.
            altered = "1.1.1.0/24\n8.8.8.0/24\n"
            (state / "candidate.txt").write_text(altered, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                validate_publication(state, altered)

            # Simulate crash after replacing both candidate files but before manifest.
            altered_bird = render_bird(parse_prefixes(altered))
            (state / "candidate.bird.conf").write_text(altered_bird, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                validate_publication(state, altered)

            # Last-written manifest completes publication.
            manifest["candidate_sha256"] = hashlib.sha256(altered.encode()).hexdigest()
            manifest["bird_sha256"] = hashlib.sha256(altered_bird.encode()).hexdigest()
            (state / "candidate.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(validate_publication(state, altered), altered)

    def test_old_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            text = "8.8.8.0/24\n"
            (state / "candidate.bird.conf").write_text(render_bird(parse_prefixes(text)))
            (state / "candidate.json").write_text('{"status":"candidate_not_applied","prefix_count":1}')
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                validate_publication(state, text)
