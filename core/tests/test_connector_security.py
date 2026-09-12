import hashlib
import hmac
import unittest
from datetime import UTC, datetime, timedelta

from autonomous_mes.application.connector_security import HmacConnectorAuthenticator
from autonomous_mes.domain.errors import Forbidden, IdempotencyConflict
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class ConnectorSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.auth = HmacConnectorAuthenticator(self.store, {"factory-a": "test-secret"})
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.timestamp = str(int(self.now.timestamp()))
        self.nonce = "nonce-1234567890"
        self.body = b'{"sourceSystem":"DNC","resources":[]}'

    def signature(self, body: bytes | None = None) -> str:
        signed = (
            self.timestamp.encode()
            + b"\n"
            + self.nonce.encode()
            + b"\n"
            + (body or self.body)
        )
        return hmac.new(b"test-secret", signed, hashlib.sha256).hexdigest()

    def test_accepts_valid_signature_and_rejects_replay(self) -> None:
        receipt = self.auth.authenticate(
            "factory-a", self.timestamp, self.nonce, self.signature(), self.body, self.now
        )
        self.assertEqual(hashlib.sha256(self.body).hexdigest(), receipt.request_digest)
        with self.assertRaises(IdempotencyConflict):
            self.auth.authenticate(
                "factory-a", self.timestamp, self.nonce, self.signature(), self.body, self.now
            )

    def test_rejects_tampering_unknown_key_and_stale_timestamp(self) -> None:
        with self.assertRaises(Forbidden):
            self.auth.authenticate(
                "factory-a", self.timestamp, self.nonce, self.signature(), b"tampered", self.now
            )
        with self.assertRaises(Forbidden):
            self.auth.authenticate(
                "unknown", self.timestamp, self.nonce, self.signature(), self.body, self.now
            )
        with self.assertRaises(Forbidden):
            self.auth.authenticate(
                "factory-a",
                self.timestamp,
                "another-nonce-1234",
                self.signature(),
                self.body,
                self.now + timedelta(minutes=6),
            )


if __name__ == "__main__":
    unittest.main()
