import unittest
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from autonomous_mes.application.identity import Identity, OidcTokenVerifier
from autonomous_mes.domain.errors import Forbidden


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.private_key.public_key()
        cls.verifier = OidcTokenVerifier(
            "https://identity.example.test/realms/agentic-mes",
            "agentic-mes-api",
            "https://identity.example.test/certs",
            signing_key_resolver=lambda _: cls.public_key,
        )

    def _token(self, **overrides: object) -> str:
        now = datetime.now(UTC)
        claims: dict[str, object] = {
            "sub": "supervisor-7",
            "name": "Shift Supervisor",
            "iss": "https://identity.example.test/realms/agentic-mes",
            "aud": "agentic-mes-api",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "realm_access": {"roles": ["SUPERVISOR", "OPERATOR"]},
            "factory_ids": ["FACTORY-DEMO", "FACTORY-02"],
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    def test_verifies_rs256_and_maps_identity_scope(self) -> None:
        identity = self.verifier.authenticate(self._token())

        self.assertEqual("supervisor-7", identity.subject_id)
        self.assertEqual("Shift Supervisor", identity.display_name)
        self.assertEqual(frozenset({"SUPERVISOR", "OPERATOR"}), identity.roles)
        self.assertEqual(frozenset({"FACTORY-DEMO", "FACTORY-02"}), identity.factory_ids)
        identity.require_role("SUPERVISOR")
        identity.require_factory("FACTORY-DEMO")

    def test_rejects_wrong_audience(self) -> None:
        with self.assertRaises(Forbidden):
            self.verifier.authenticate(self._token(aud="some-other-api"))

    def test_rejects_expired_token(self) -> None:
        with self.assertRaises(Forbidden):
            self.verifier.authenticate(
                self._token(exp=datetime.now(UTC) - timedelta(seconds=1))
            )

    def test_role_and_factory_authorization_are_independent(self) -> None:
        identity = Identity("operator-2", "Operator", frozenset({"OPERATOR"}), frozenset())
        with self.assertRaises(Forbidden):
            identity.require_role("SUPERVISOR")
        with self.assertRaises(Forbidden):
            identity.require_factory("FACTORY-DEMO")
