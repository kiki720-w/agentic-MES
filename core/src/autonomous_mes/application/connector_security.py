import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from autonomous_mes.domain.errors import Forbidden, ValidationError

from .ports import ConnectorSecurityStore


@dataclass(frozen=True)
class ConnectorReceipt:
    nonce: str
    key_id: str
    request_digest: str
    received_at: datetime


class HmacConnectorAuthenticator:
    def __init__(
        self,
        store: ConnectorSecurityStore,
        credentials: dict[str, str],
        max_clock_skew_seconds: int = 300,
    ) -> None:
        self._store = store
        self._credentials = credentials
        self._max_clock_skew_seconds = max_clock_skew_seconds

    def authenticate(
        self,
        key_id: str,
        timestamp: str,
        nonce: str,
        signature: str,
        body: bytes,
        now: datetime | None = None,
    ) -> ConnectorReceipt:
        secret = self._credentials.get(key_id)
        if not secret:
            raise Forbidden("connector key is not configured")
        try:
            sent_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
        except (ValueError, OSError) as exc:
            raise ValidationError("connector timestamp must be Unix seconds") from exc
        observed_at = now or datetime.now(UTC)
        if abs((observed_at - sent_at).total_seconds()) > self._max_clock_skew_seconds:
            raise Forbidden("connector timestamp is outside the allowed window")
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", nonce):
            raise ValidationError("connector nonce must contain 16 to 128 safe characters")
        signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature.lower()):
            raise Forbidden("connector signature is invalid")
        receipt = ConnectorReceipt(
            nonce,
            key_id,
            hashlib.sha256(body).hexdigest(),
            observed_at,
        )
        self._store.record_connector_receipt(receipt)
        return receipt
