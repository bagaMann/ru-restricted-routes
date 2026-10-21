"""Fault injection against apply's rollback path; no production BIRD calls."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ru_routes.apply import apply_locked
from ru_routes.generate import parse_prefixes, render_bird


def fixture(root):
    state = Path(root)
    baseline = "1.1.1.0/24\n4.4.4.0/24\n8.8.4.0/24\n8.8.8.0/24\n9.9.9.0/24\n"
    proposed = baseline + "11.11.11.0/24\n"
    original = render_bird(parse_prefixes(baseline))
    updated = render_bird(parse_prefixes(proposed))
    candidate = state / "candidate.bird.conf"
    candidate.write_text(updated)
    (state / "candidate.txt").write_text(proposed)
    (state / "current.txt").write_text(baseline)
    (state / "candidate.json").write_text(json.dumps({
        "status": "candidate_not_applied", "prefix_count": 6,
        "candidate_sha256": hashlib.sha256(proposed.encode()).hexdigest(),
        "bird_sha256": hashlib.sha256(updated.encode()).hexdigest(),
    }))
    target = state / "routes.conf"
    target.write_text(original)
    main = state / "bird.conf"
    main.write_text(f'router id 192.0.2.1;\ninclude "{target}";\n')
    args = argparse.Namespace(candidate=candidate, target=target,
                              bird_config=main, backup=state / "backup.conf",
                              confirm=True, promote_current=True)
    return args, original, baseline


class RollbackTests(unittest.TestCase):
    def setUp(self):
        verifier = patch("ru_routes.verify_routes.read_and_verify", return_value=5)
        verifier.start()
        self.addCleanup(verifier.stop)

    def test_configure_failure_restores_original(self):
        with TemporaryDirectory() as folder:
            args, original, baseline = fixture(folder)
            calls = []
            def command(*argv):
                calls.append(argv)
                if argv == ("birdc", "configure") and calls.count(argv) == 1:
                    raise subprocess.CalledProcessError(1, argv)
            with patch("ru_routes.apply.read_bird_state", return_value=(5, 5)):
                with patch("ru_routes.apply.run", side_effect=command):
                    with self.assertRaises(subprocess.CalledProcessError):
                        apply_locked(args)
            self.assertEqual(args.target.read_text(), original)
            self.assertEqual(args.backup.read_text(), original)
            self.assertEqual((Path(folder) / "current.txt").read_text(), baseline)
            self.assertEqual(calls.count(("birdc", "configure")), 2)

    def test_export_mismatch_restores_original(self):
        with TemporaryDirectory() as folder:
            args, original, baseline = fixture(folder)
            with patch("ru_routes.apply.read_bird_state", return_value=(5, 5)):
                with patch("ru_routes.apply.run"):
                    with patch("ru_routes.apply.wait_for_routes", side_effect=[RuntimeError("export mismatch"), None]) as wait:
                        with self.assertRaisesRegex(RuntimeError, "export mismatch"):
                            apply_locked(args)
            self.assertEqual([call.args[0] for call in wait.call_args_list], [6, 5])
            self.assertEqual(args.target.read_text(), original)
            self.assertEqual((Path(folder) / "current.txt").read_text(), baseline)

    def test_rollback_verification_failure_is_reported(self):
        with TemporaryDirectory() as folder:
            args, original, baseline = fixture(folder)
            with patch("ru_routes.apply.read_bird_state", return_value=(5, 5)):
                with patch("ru_routes.apply.run"):
                    with patch("ru_routes.apply.wait_for_routes", side_effect=[
                        RuntimeError("export mismatch"), RuntimeError("rollback verification failed")
                    ]):
                        with self.assertRaisesRegex(RuntimeError, "rollback could not be verified"):
                            apply_locked(args)
            self.assertEqual(args.target.read_text(), original)
            self.assertEqual((Path(folder) / "current.txt").read_text(), baseline)


if __name__ == "__main__":
    unittest.main()
