"""Validate and HMAC-sign a scheduling snapshot for the control-plane connector."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


def load_payload(path: Path) -> tuple[bytes, dict[str, Any]]:
    body = path.read_bytes()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TypeError("snapshot root must be an object")
    required = {
        "sourceSystem",
        "workshopId",
        "sourceRevision",
        "observedAt",
        "workOrders",
        "resources",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")
    if not isinstance(payload["workOrders"], list) or not isinstance(payload["resources"], list):
        raise TypeError("workOrders and resources must be arrays")
    return body, payload


def build_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    signed = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def validate_endpoint(endpoint: str) -> None:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint must be an absolute HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("plain HTTP is allowed only for a loopback development endpoint")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("snapshot", type=Path)
    result.add_argument(
        "--endpoint",
        default="http://127.0.0.1:8000/api/v1/connectors/v1/scheduling-snapshots",
    )
    result.add_argument("--key-id", default=os.getenv("AUTONOMOUS_MES_CONNECTOR_KEY_ID"))
    result.add_argument("--secret", default=os.getenv("AUTONOMOUS_MES_CONNECTOR_HMAC_SECRET"))
    result.add_argument("--validate-only", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        body, payload = load_payload(args.snapshot)
        validate_endpoint(args.endpoint)
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    summary = {
        "sourceSystem": payload["sourceSystem"],
        "workshopId": payload["workshopId"],
        "sourceRevision": payload["sourceRevision"],
        "workOrderCount": len(payload["workOrders"]),
        "resourceCount": len(payload["resources"]),
        "bodySha256": hashlib.sha256(body).hexdigest(),
    }
    if args.validate_only:
        print(json.dumps({"valid": True, **summary}, ensure_ascii=False))
        return 0
    if not args.key_id or not args.secret:
        raise SystemExit("connector key ID and secret are required; use arguments or environment variables")

    timestamp = str(int(time.time()))
    nonce = uuid4().hex
    signature = build_signature(args.secret, timestamp, nonce, body)
    request = Request(
        args.endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Connector-Key": args.key_id,
            "X-Connector-Timestamp": timestamp,
            "X-Connector-Nonce": nonce,
            "X-Connector-Signature": signature,
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise SystemExit(f"connector rejected snapshot ({exc.code}): {detail}") from exc
    print(json.dumps({"accepted": True, **summary, "response": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
