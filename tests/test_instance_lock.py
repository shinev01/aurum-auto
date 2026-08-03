import tempfile
import unittest
from pathlib import Path

from aurum_bot.instance_lock import InstanceLock


class InstanceLockTests(unittest.TestCase):
    def test_second_instance_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.lock"
            with InstanceLock(path):
                with self.assertRaises(RuntimeError):
                    with InstanceLock(path):
                        pass


if __name__ == "__main__":
    unittest.main()
