import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ru_routes.apply import apply_locked
from ru_routes.generate import parse_prefixes, render_bird


class ApplyGateTests(unittest.TestCase):
    def test_reject_corrupt_manifest_without_touching_bird(self):
        with TemporaryDirectory() as folder:
            state = Path(folder)
            candidate = state / "candidate.bird.conf"
            text = "8.8.8.0/24\n"
            bird = render_bird(parse_prefixes(text))
            candidate.write_text(bird)
            (state / "candidate.txt").write_text(text)
            (state / "current.txt").write_text(text)
            (state / "candidate.json").write_text(json.dumps({
                "status": "candidate_not_applied", "prefix_count": 1,
                "candidate_sha256": "invalid",
                "bird_sha256": hashlib.sha256(bird.encode()).hexdigest(),
            }))
            target = state / "target.conf"
            target.write_text(bird)
            main = state / "bird.conf"
            main.write_text("")
            args = argparse.Namespace(candidate=candidate, target=target,
                                      bird_config=main, confirm=True,
                                      promote_current=False, backup=state / "backup")
            with patch("ru_routes.apply.run") as command:
                with self.assertRaisesRegex(ValueError, "digest mismatch"):
                    apply_locked(args)
                command.assert_not_called()
            self.assertEqual(target.read_text(), bird)

    def test_reject_large_change_before_apply(self):
        with TemporaryDirectory() as folder:
            state = Path(folder)
            old = "8.8.8.0/24\n1.1.1.0/24\n"
            new = old + "9.9.9.0/24\n"
            bird = render_bird(parse_prefixes(new))
            (state / "candidate.bird.conf").write_text(bird)
            (state / "candidate.txt").write_text(new)
            (state / "current.txt").write_text(old)
            (state / "candidate.json").write_text(json.dumps({
                "status": "candidate_not_applied", "prefix_count": 3,
                "candidate_sha256": hashlib.sha256(new.encode()).hexdigest(),
                "bird_sha256": hashlib.sha256(bird.encode()).hexdigest(),
            }))
            target = state / "target.conf"
            target.write_text(render_bird(parse_prefixes(old)))
            main = state / "bird.conf"
            main.write_text("")
            args = argparse.Namespace(candidate=state / "candidate.bird.conf",
                                      target=target, bird_config=main, confirm=True,
                                      promote_current=False, backup=state / "backup")
            with patch("ru_routes.apply.read_bird_state", return_value=(2, 2)):
                with patch("ru_routes.apply.run") as command:
                    with self.assertRaisesRegex(ValueError, "review rejected"):
                        apply_locked(args)
                    command.assert_not_called()
