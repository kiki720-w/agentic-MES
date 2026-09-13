from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from autonomous_mes.application.model_gateway import ModelGatewayError
from autonomous_mes.infrastructure.deepseek_gateway import (
    ConfigurableModelGateway,
    OpenAICompatibleDiagnosticModel,
)
from autonomous_mes.infrastructure.egress import BusinessReadTransport, EgressDenied, EgressPolicy


def test_cloud_mes_does_not_authorize_model_even_with_local_provider_label():
    policy = EgressPolicy(business_endpoints=("https://mes.example/api",))
    with patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post") as post:
        model = OpenAICompatibleDiagnosticModel("", "test", "https://mes.example/api",
                                                provider="OLLAMA", egress_policy=policy)
        with pytest.raises(ModelGatewayError):
            model.answer("private production facts", {"order": "private"})
        post.assert_not_called()
    assert model.status()["lastError"] == "EgressDenied"


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1", "http://127.0.0.1:11435/v1",
    "http://127.0.0.1:11434/other", "http://127.0.0.1:11434/v1/../other",
    "http://user:secret@127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1?x=1",
    "http://127.0.0.1:11434/v1#test", "http://127.0.0.1:11434/%76%31",
    "http://127.0.0.1:11434/v1\\other", "http://169.254.169.254/v1",
])
def test_model_exact_destination_cannot_be_bypassed(url):
    gateway = ConfigurableModelGateway()
    with pytest.raises(EgressDenied):
        gateway.configure("OLLAMA", "test", url, 12, None, verify_connection=False)


def test_local_switch_does_not_forward_cloud_key_and_requires_no_key():
    gateway = ConfigurableModelGateway("cloud-secret", egress_policy=EgressPolicy(
        model_cloud_endpoints=("https://api.deepseek.com",)))
    with patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post") as post:
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"answer":"ok"}'}}]}
        status = gateway.configure("LOCAL", "local", "http://127.0.0.1:11434/v1", 12,
                                   None, verify_connection=True)
        assert status["connectionStatus"] == "VERIFIED"
        assert status["apiKeyConfigured"] is False
        assert post.call_args.kwargs["headers"] == {}
        assert post.call_args.kwargs["trust_env"] is False
        assert post.call_args.kwargs["follow_redirects"] is False


def test_legacy_cloud_key_is_blocked_without_explicit_authorization():
    with patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post") as post:
        gateway = ConfigurableModelGateway("old-secret")
        assert gateway.status()["connectionStatus"] == "BLOCKED"
        with pytest.raises(ModelGatewayError):
            gateway.answer("test", {})
        post.assert_not_called()


def test_local_environment_configuration_without_key():
    gateway = ConfigurableModelGateway(model="local", provider="LOCAL",
                                       base_url="http://127.0.0.1:11434/v1")
    assert gateway.status()["connectionStatus"] == "CONFIGURED"


def test_business_transport_requires_separate_authorization_and_stops_redirects():
    url = "https://mes.example/api"
    with pytest.raises(EgressDenied):
        BusinessReadTransport(EgressPolicy(model_cloud_endpoints=(url,)), url)
    transport = BusinessReadTransport(EgressPolicy(business_endpoints=(url,)), url)
    with patch("autonomous_mes.infrastructure.egress.httpx.get") as get:
        get.return_value = httpx.Response(302, headers={"location": "https://other.example"},
                                         request=httpx.Request("GET", url + "/orders"))
        with pytest.raises(httpx.HTTPStatusError):
            transport.get("orders")
        assert get.call_count == 1
        assert get.call_args.kwargs["trust_env"] is False
        assert get.call_args.kwargs["follow_redirects"] is False
        for resource in ("../orders", "https://other.example", "/orders", "%2e%2e/orders"):
            with pytest.raises(EgressDenied):
                transport.get(resource)
        assert get.call_count == 1


def test_api_denies_cloud_even_for_admin_and_reports_policy():
    from autonomous_mes.api import app

    client = TestClient(app)
    with patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post") as post:
        response = client.put("/api/v1/system/model-gateway/configuration", json={
            "provider": "DEEPSEEK", "model": "test", "baseUrl": "https://api.deepseek.com",
            "apiKey": "test-placeholder", "verifyConnection": True,
        })
        assert response.status_code == 409
        post.assert_not_called()
    status = client.get("/api/v1/agent/model-status").json()
    assert status["networkPolicy"]["modelMode"] == "LOCAL_ONLY"
    assert status["networkPolicy"]["businessConnectorStatus"] == "NOT_INTEGRATED"


@pytest.mark.parametrize("kwargs", [
    {"model_local_endpoints": ("http://factory.local/v1",)},
    {"model_local_endpoints": ("http://169.254.169.254/v1",)},
    {"model_cloud_endpoints": ("http://cloud.example/v1",)},
    {"business_endpoints": ("http://mes.example/api",)},
])
def test_invalid_deployment_policy_fails_closed(kwargs):
    with pytest.raises(EgressDenied):
        EgressPolicy(**kwargs)
