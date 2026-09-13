import json
from threading import Event
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from autonomous_mes.application.capability_benchmark import CASES, CapabilityBenchmarks
from autonomous_mes.application.model_gateway import ModelGatewayError, NaturalLanguageAnswer
from autonomous_mes.infrastructure.deepseek_gateway import (
    ConfigurableModelGateway,
    OpenAICompatibleDiagnosticModel,
)


def candidate():
    model = Mock(spec=OpenAICompatibleDiagnosticModel)
    model.status.return_value = {"provider": "TEST", "model": "candidate", "lastError": None}
    model.answer.side_effect = [NaturalLanguageAnswer(c["expected"], "TEST", "candidate") for c in CASES]
    model.redact.side_effect = lambda text: text.replace("private-key-placeholder", "[REDACTED]")
    return model


def test_full_benchmark_evidence_and_versioned_comparison(tmp_path):
    service = CapabilityBenchmarks(tmp_path)
    first = service.start(candidate(), "planner", "ACTIVE")
    service._executor.shutdown(wait=True)
    report = service.get(first["runId"])
    assert report["scorePercent"] == 100
    assert report["completed"] == 9
    assert report["requestFailures"] == 0
    assert report["checks"][0]["actual"] == CASES[0]["expected"]
    assert report["checks"][0]["facts"] == CASES[0]["facts"]
    assert "checks" not in service.list()[0]
    second = CapabilityBenchmarks(tmp_path)
    run = second.start(candidate(), "planner", "LOCAL")
    second._executor.shutdown(wait=True)
    assert second.compare(first["runId"], run["runId"])["comparable"]
    modified = second.get(run["runId"])
    modified["fixtureSha256"] = "different"
    second._save(modified)
    assert not second.compare(first["runId"], run["runId"])["comparable"]
    with pytest.raises(ValueError):
        second.get("../../.env")


def test_wrong_answers_are_visible_and_secrets_are_redacted(tmp_path):
    service = CapabilityBenchmarks(tmp_path)
    model = candidate()
    model.answer.side_effect = None
    model.answer.return_value = NaturalLanguageAnswer("wrong private-key-placeholder", "TEST", "candidate")
    run = service.start(model, "planner", "ACTIVE")
    service._executor.shutdown(wait=True)
    report = service.get(run["runId"])
    assert report["scorePercent"] == 0
    assert report["checks"][0]["actual"] == "wrong [REDACTED]"
    assert "private-key-placeholder" not in json.dumps(report)


def test_transport_failure_never_becomes_a_model_score_pass(tmp_path):
    service = CapabilityBenchmarks(tmp_path)
    model = candidate()
    model.answer.side_effect = ModelGatewayError("private transport detail")
    model.status.return_value["lastError"] = "ConnectError"
    run = service.start(model, "planner", "LOCAL")
    service._executor.shutdown(wait=True)
    report = service.get(run["runId"])
    assert report["scorePercent"] is None
    assert report["status"] == "FAILED"
    assert report["requestFailures"] == len(CASES)
    assert all(c["actual"] is None and c["error"] == "ConnectError" for c in report["checks"])
    assert "private transport detail" not in json.dumps(report)


def test_duplicate_jobs_rejected_and_restart_marks_interrupted(tmp_path):
    entered, release = Event(), Event()
    model = candidate()

    def answer(*args):
        entered.set()
        assert release.wait(3)
        return NaturalLanguageAnswer("wrong", "TEST", "candidate")

    model.answer.side_effect = answer
    service = CapabilityBenchmarks(tmp_path)
    run = service.start(model, "planner", "ACTIVE")
    assert entered.wait(3)
    try:
        with pytest.raises(ValueError, match="运行中"):
            service.start(model, "planner", "ACTIVE")
    finally:
        release.set()
        service._executor.shutdown(wait=True)
    report = service.get(run["runId"])
    report["status"] = "RUNNING"
    service._save(report)
    restarted = CapabilityBenchmarks(tmp_path)
    restarted.recover_interrupted()
    assert restarted.get(run["runId"])["status"] == "INTERRUPTED"
    restarted._executor.shutdown(wait=True)


def test_local_adapter_never_sends_authorization_and_model_capture_is_stable():
    local = OpenAICompatibleDiagnosticModel("", "local-model", "http://127.0.0.1:11434/v1", provider="LOCAL_BENCHMARK")
    with patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post") as post:
        post.return_value.json.return_value = {"choices": [{"message": {"content": '{"answer":"ok"}'}}]}
        local.answer("test", {})
        assert post.call_args.kwargs["headers"] == {}
        assert post.call_args.kwargs["trust_env"] is False
        assert "tools" not in post.call_args.kwargs["json"]
    gateway = ConfigurableModelGateway("private-key-placeholder", model="first")
    captured = gateway.evaluation_adapter()
    gateway.configure("DEEPSEEK", "second", "https://api.deepseek.com", 12, None)
    assert captured.status()["model"] == "first"
    assert captured.redact("private-key-placeholder") == "[REDACTED]"


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://localhost:11434/v1",
                                 "http://127.0.0.1:11434/v1?key=test", "http://user:pass@127.0.0.1:11434/v1",
                                 "http://127.0.0.1:80/v1", "http://127.0.0.1:11434/other"])
def test_local_benchmark_rejects_nonliteral_loopback_and_credentials(url):
    from autonomous_mes.api import app

    with patch("autonomous_mes.api.capability_benchmarks.start") as start:
        response = TestClient(app).post("/api/v1/agent/capability-runs", json={
            "target": "LOCAL", "baseUrl": url, "model": "test"})
        assert response.status_code == 409
        start.assert_not_called()
