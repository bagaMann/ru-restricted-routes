"""Offline advisory-lock integration tests. Never touches production BIRD."""
import fcntl
import multiprocessing
from pathlib import Path
import tempfile
import unittest


def try_lock(path, mode, queue):
    with open(path, "a+") as file:
        try:
            fcntl.flock(file, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            queue.put(False)
        else:
            queue.put(True)


class LockIntegrationTests(unittest.TestCase):
    def assert_child_lock(self, parent_mode, child_mode, expected):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "routes.lock")
            with open(path, "a+") as parent:
                fcntl.flock(parent, parent_mode)
                ctx = multiprocessing.get_context("spawn")
                queue = ctx.Queue()
                process = ctx.Process(target=try_lock, args=(path, child_mode, queue))
                process.start()
                try:
                    self.assertEqual(queue.get(timeout=10), expected)
                    process.join(timeout=10)
                    self.assertEqual(process.exitcode, 0)
                finally:
                    if process.is_alive():
                        process.terminate()
                        process.join()
                    queue.close()

    def test_writer_blocks_writer(self):
        self.assert_child_lock(fcntl.LOCK_EX, fcntl.LOCK_EX, False)

    def test_writer_blocks_reader(self):
        self.assert_child_lock(fcntl.LOCK_EX, fcntl.LOCK_SH, False)

    def test_reader_blocks_writer(self):
        self.assert_child_lock(fcntl.LOCK_SH, fcntl.LOCK_EX, False)

    def test_reader_allows_reader(self):
        self.assert_child_lock(fcntl.LOCK_SH, fcntl.LOCK_SH, True)


if __name__ == "__main__":
    unittest.main()
