from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import jwt

from autonomous_mes.domain.errors import Forbidden


@dataclass(frozen=True)
class Identity:
    subject_id: str
    display_name: str
    roles: frozenset[str]
    factory_ids: frozenset[str]

    def require_role(self, role: str) -> None:
        if role not in self.roles:
            raise Forbidden(f"identity requires role {role}")

    def require_any_role(self, *roles: str) -> None:
        if not self.roles.intersection(roles):
            raise Forbidden(f"identity requires one of roles: {', '.join(roles)}")

    def require_factory(self, factory_id: str) -> None:
        if factory_id not in self.factory_ids and "*" not in self.factory_ids:
            raise Forbidden("identity is not authorized for this factory")

    def as_dict(self) -> dict[str, object]:
        return {
            "subjectId": self.subject_id,
            "displayName": self.display_name,
            "roles": sorted(self.roles),
            "factoryIds": sorted(self.factory_ids),
        }


def parse_csv_set(value: str) -> frozenset[str]:
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def claim_at_path(claims: Mapping[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


class DeveloperIdentityProvider:
    def __init__(self, identity: Identity) -> None:
        self._identity = identity

    def authenticate(self, _: str | None = None) -> Identity:
        return self._identity


class OidcTokenVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str,
        roles_claim: str = "realm_access.roles",
        factory_ids_claim: str = "factory_ids",
        signing_key_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._audience = audience
        self._roles_claim = roles_claim
        self._factory_ids_claim = factory_ids_claim
        self._jwks_client = jwt.PyJWKClient(jwks_url)
        self._signing_key_resolver = signing_key_resolver

    def authenticate(self, token: str | None) -> Identity:
        if not token:
            raise Forbidden("bearer token is required")
        try:
            signing_key = (
                self._signing_key_resolver(token)
                if self._signing_key_resolver
                else self._jwks_client.get_signing_key_from_jwt(token).key
            )
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise Forbidden("bearer token is invalid") from exc

        roles = self._string_set_claim(claim_at_path(claims, self._roles_claim))
        factories = self._string_set_claim(claim_at_path(claims, self._factory_ids_claim))
        subject_id = claims.get("sub")
        if not isinstance(subject_id, str) or not subject_id:
            raise Forbidden("bearer token subject is invalid")
        display_name = claims.get("name") or claims.get("preferred_username") or subject_id
        if not isinstance(display_name, str):
            display_name = subject_id
        return Identity(subject_id, display_name, roles, factories)

    @staticmethod
    def _string_set_claim(value: Any) -> frozenset[str]:
        if isinstance(value, str):
            return frozenset({value})
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return frozenset(value)
        return frozenset()
