import json
from unittest.mock import Mock, patch

import pytest

from autonomous_mes.application.capability_check import CASES, run_text_check
from autonomous_mes.application.model_gateway import ModelGatewayError, NaturalLanguageAnswer
from autonomous_mes.infrastructure.deepseek_gateway import ConfigurableModelGateway


def test_benchmark_persists_reproducible_evidence_without_answer_text(tmp_path):
    model = Mock()
    model.answer.side_effect = [NaturalLanguageAnswer(c[3], "TEST", "model") for c in CASES]
    result = run_text_check(model, "operator-1", {"provider": "TEST"}, tmp_path)
    assert result["passed"] is True
    assert len(result["checks"]) == 3
    saved = json.loads((tmp_path / f"{result['runId']}.json").read_text(encoding="utf-8"))
    assert saved == result
    assert all(c["answerSha256"] for c in saved["checks"])
    assert "answer" not in saved["checks"][0]
    assert saved["dataScope"] == "SYNTHETIC_ONLY"


def test_failure_and_wrong_answer_never_pass_benchmark(tmp_path):
    model = Mock()
    model.answer.side_effect = [ModelGatewayError("private provider detail"),
                               NaturalLanguageAnswer("invented", "TEST", "model"),
                               NaturalLanguageAnswer("81", "TEST", "model")]
    result = run_text_check(model, "operator-1", {}, tmp_path)
    assert result["passed"] is False
    assert not any(c["passed"] for c in result["checks"])
    assert "private provider detail" not in json.dumps(result)


@pytest.mark.parametrize("choice,error", [
    ({"finish_reason": "length", "message": {"content": '{"answer":"partial"}'}},
     "TruncatedModelResponse"),
    ({"message": {"content": "", "reasoning_content": "not a final answer"}},
     "EmptyModelResponse"),
    ({"message": {"content": '{"answer":null}'}}, "TypeError"),
    ({"message": {"content": '{"answer":{"text":"bad"}}'}}, "TypeError"),
    ({"message": {"content": 'not json'}}, "ValueError"),
])
def test_invalid_output_is_degraded_and_recoverable(choice, error):
    gateway = ConfigurableModelGateway("test-placeholder")
    assert gateway.status()["connectionStatus"] == "CONFIGURED"
    with patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post") as post:
        post.return_value.json.return_value = {"choices": [choice]}
        with pytest.raises(ModelGatewayError):
            gateway.answer("synthetic test", {})
        assert gateway.status()["connectionStatus"] == "DEGRADED"
        assert gateway.status()["lastError"] == error
        assert gateway.status()["lastSuccessAt"] is None
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": '{"answer":"ok"}'}}]}
        gateway.verify()
        assert gateway.status()["connectionStatus"] == "VERIFIED"
        assert gateway.status()["lastSuccessAt"]
        assert gateway.status()["failureCount"] == 1
    assert gateway.disable()["connectionStatus"] == "DISABLED"


def test_failed_candidate_does_not_replace_active_model():
    gateway = ConfigurableModelGateway("test-placeholder", model="original")
    with patch("autonomous_mes.infrastructure.deepseek_gateway.httpx.post") as post:
        post.return_value.json.return_value = {"choices": [{"message": {"content": ""}}]}
        with pytest.raises(ModelGatewayError):
            gateway.configure("DEEPSEEK", "candidate", "https://api.deepseek.com", 12,
                              None, verify_connection=True)
    assert gateway.status()["model"] == "original"
    assert gateway.status()["connectionStatus"] == "CONFIGURED"


def test_check_endpoint_requires_identity_and_never_calls_chat_service():
    from fastapi.testclient import TestClient

    from autonomous_mes.api import app, current_identity
    from autonomous_mes.application.identity import Identity

    with (
        patch("autonomous_mes.api.run_text_check", return_value={"passed": True}) as run,
        patch("autonomous_mes.api.natural_language_service.ask") as chat,
    ):
        response = TestClient(app).post("/api/v1/agent/capability-check",
                                        headers={"X-Dev-Actor": "demo-planner"})
        assert response.status_code == 200
        assert run.call_args.args[1] == "demo-planner"
        chat.assert_not_called()
        app.dependency_overrides[current_identity] = lambda: Identity(
            "viewer", "Viewer", frozenset(), frozenset({"FACTORY-DEMO"}))
        try:
            assert TestClient(app).post("/api/v1/agent/capability-check").status_code == 403
            assert run.call_count == 1
        finally:
            app.dependency_overrides.clear()
