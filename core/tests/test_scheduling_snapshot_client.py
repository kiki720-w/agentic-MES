import hashlib
import hmac
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "push-scheduling-snapshot.py"
SPEC = importlib.util.spec_from_file_location("push_scheduling_snapshot", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SchedulingSnapshotClientTests(unittest.TestCase):
    def test_signature_matches_connector_contract(self) -> None:
        body = b'{"sourceSystem":"edge","workOrders":[],"resources":[]}'
        actual = MODULE.build_signature("secret", "123", "nonce-1234567890", body)
        expected = hmac.new(
            b"secret", b"123\nnonce-1234567890\n" + body, hashlib.sha256
        ).hexdigest()

        self.assertEqual(expected, actual)

    def test_plain_http_is_restricted_to_local_development(self) -> None:
        MODULE.validate_endpoint("http://127.0.0.1:8000/api")
        MODULE.validate_endpoint("https://factory.example/api")

        with self.assertRaisesRegex(ValueError, "loopback"):
            MODULE.validate_endpoint("http://factory.example/api")


if __name__ == "__main__":
    unittest.main()
