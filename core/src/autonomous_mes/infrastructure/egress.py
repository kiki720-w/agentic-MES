"""Independent, immutable application egress boundaries. Not a host firewall."""

from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

import httpx


class EgressDenied(ValueError):
    """A destination is not authorized for this channel."""


def endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query or parsed.fragment
        or "\\" in value or "%" in value
        or any(ord(char) <= 32 for char in value)
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise EgressDenied("Invalid endpoint URL")
    port = parsed.port  # Validate malformed/out-of-range ports.
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    authority = host + (f":{port}" if port else "")
    return f"{parsed.scheme}://{authority}{parsed.path.rstrip('/')}"


def local_address(url: str) -> bool:
    try:
        address = ip_address(urlsplit(url).hostname or "")
    except ValueError:
        return False  # No DNS lookup: avoid local-name DNS rebinding.
    return address.is_loopback or any(address in ip_network(network) for network in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
    ))


@dataclass(frozen=True)
class EgressPolicy:
    model_local_endpoints: tuple[str, ...] = ("http://127.0.0.1:11434/v1",)
    model_cloud_endpoints: tuple[str, ...] = ()
    business_endpoints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("model_local_endpoints", "model_cloud_endpoints", "business_endpoints"):
            object.__setattr__(self, name, tuple(endpoint(url) for url in getattr(self, name)))
        if any(not local_address(url) for url in self.model_local_endpoints):
            raise EgressDenied("Local models require literal loopback or private IP addresses")
        if any(urlsplit(url).scheme != "https" for url in self.model_cloud_endpoints):
            raise EgressDenied("Cloud model endpoints require HTTPS")
        if any(urlsplit(url).scheme != "https" and not local_address(url)
               for url in self.business_endpoints):
            raise EgressDenied("Remote business endpoints require HTTPS")

    def require_model(self, url: str) -> None:
        if endpoint(url) not in self.model_local_endpoints + self.model_cloud_endpoints:
            raise EgressDenied("模型地址未获服务端授权；云端 MES 授权不包含云模型授权。")

    def require_business(self, url: str) -> None:
        if endpoint(url) not in self.business_endpoints:
            raise EgressDenied("业务系统地址未获服务端授权。")

    def status(self) -> dict[str, object]:
        return {
            "enforcement": "APPLICATION_TRANSPORT", "configurationSource": "SERVER_ENVIRONMENT",
            "modelMode": "CLOUD_OPT_IN" if self.model_cloud_endpoints else "LOCAL_ONLY",
            "modelLocalEndpoints": list(self.model_local_endpoints),
            "modelCloudEndpoints": list(self.model_cloud_endpoints),
            "businessEndpoints": list(self.business_endpoints),
            "cloudFallback": False, "businessConnectorStatus": "NOT_INTEGRATED",
            "hostFirewallEnforced": False,
        }


class BusinessReadTransport:
    """Building block for an authorized connector; no writes or arbitrary proxy API."""

    def __init__(self, policy: EgressPolicy, base_url: str) -> None:
        policy.require_business(base_url)
        self._policy = policy
        self._base_url = endpoint(base_url)

    def get(self, resource: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
        self._policy.require_business(self._base_url)
        if not resource or any(not (part.isascii() and part.replace("-", "").replace("_", "").isalnum())
                               for part in resource.split("/")):
            raise EgressDenied("Business resource must contain plain relative path segments")
        response = httpx.get(f"{self._base_url}/{resource}", headers=headers,
                             timeout=15, trust_env=False, follow_redirects=False)
        response.raise_for_status()
        return response
