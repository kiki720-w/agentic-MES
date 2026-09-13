import json
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import httpx

from autonomous_mes.application.model_gateway import (
    DiagnosticFacts,
    DiagnosticNarrative,
    ModelGatewayError,
    NaturalLanguageAnswer,
)
from autonomous_mes.infrastructure.egress import EgressDenied, EgressPolicy, endpoint


class TruncatedModelResponse(ValueError):
    """Output budget exhausted; partial output must never be accepted."""


class EmptyModelResponse(ValueError):
    """Provider returned no final answer."""


def _text_field(result: dict[str, Any], key: str) -> str:
    value = result[key]
    if not isinstance(value, str):
        raise TypeError("model field must be text")
    return value.strip()


def _json_object(content: str) -> dict[str, Any]:
    value = content.strip()
    if value.startswith("```"):
        first_newline = value.find("\n")
        value = value[first_newline + 1 :] if first_newline >= 0 else value
        value = value.removesuffix("```")
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response does not contain a JSON object")
    result = json.loads(value[start : end + 1])
    if not isinstance(result, dict):
        raise TypeError("model response must be a JSON object")
    return result


class OpenAICompatibleDiagnosticModel:
    """Provider-neutral explanation adapter with no MES write tools."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = 12.0,
        provider: str = "OPENAI_COMPATIBLE",
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = endpoint(base_url)
        self._egress_policy = egress_policy or EgressPolicy()
        self._timeout = timeout_seconds
        self._provider = provider.strip().upper()
        self._status_lock = Lock()
        self._connection_status = "CONFIGURED"
        self._last_checked_at: str | None = None
        self._last_error: str | None = None
        self._last_success_at: str | None = None
        self._failure_count = 0

    def status(self) -> dict[str, object]:
        with self._status_lock:
            return {
                "provider": self._provider,
                "model": self._model,
                "baseUrl": self._base_url,
                "apiKeyConfigured": bool(self._api_key),
                "connectionStatus": self._connection_status,
                "lastCheckedAt": self._last_checked_at,
                "lastError": self._last_error,
                "lastSuccessAt": self._last_success_at,
                "failureCount": self._failure_count,
                "timeoutSeconds": self._timeout,
            }

    def redact(self, text: str) -> str:
        return text.replace(self._api_key, "[REDACTED]") if self._api_key else text

    def _record_status(self, status: str, error: str | None = None) -> None:
        with self._status_lock:
            self._connection_status = status
            self._last_checked_at = datetime.now(UTC).isoformat()
            self._last_error = error
            if status == "VERIFIED":
                self._last_success_at = self._last_checked_at
            elif status == "DEGRADED":
                self._failure_count += 1

    def _request(self, messages: list[dict[str, str]], max_tokens: int) -> dict[str, Any]:
        self._egress_policy.require_model(self._base_url)
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        if self._provider == "DEEPSEEK":
            payload["thinking"] = {"type": "disabled"}
            payload["response_format"] = {"type": "json_object"}
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"} if self._api_key else {},
            json=payload,
            timeout=self._timeout,
            trust_env=False,
            follow_redirects=False,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        choice = body["choices"][0]
        if not isinstance(choice, dict):
            raise TypeError("model choice must be an object")
        if choice.get("finish_reason") == "length":
            raise TruncatedModelResponse()
        content = choice["message"]["content"]
        if content == "":
            raise EmptyModelResponse()
        if not isinstance(content, str):
            raise TypeError("model response content must be text")
        return _json_object(content)

    def explain(self, facts: DiagnosticFacts) -> DiagnosticNarrative:
        try:
            result = self._request(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是机械加工MES诊断解释器。只依据用户提供的JSON事实输出JSON，"
                            '格式为{"diagnosis":"...","recommendation":"..."}。'
                            "不得声称已执行动作，不得要求修改数据库、绕过审批或控制设备。"
                            "建议必须说明由有权限人员确认后执行。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(facts.__dict__, ensure_ascii=False)},
                ],
                1024,
            )
            diagnosis = _text_field(result, "diagnosis")
            recommendation = _text_field(result, "recommendation")
            if (
                not diagnosis
                or not recommendation
                or len(diagnosis) > 800
                or len(recommendation) > 800
            ):
                raise ValueError("invalid narrative length")
            self._record_status("VERIFIED")
            return DiagnosticNarrative(
                diagnosis, recommendation, self._provider, self._model
            )
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self._record_status("DEGRADED", exc.__class__.__name__)
            raise ModelGatewayError("model diagnostic request failed") from exc

    def answer(self, question: str, facts: dict[str, Any]) -> NaturalLanguageAnswer:
        try:
            result = self._request(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是机械加工MES只读问答助手。只能依据用户消息中的MES快照回答，"
                            '不可使用模型记忆补充生产事实。输出JSON格式 {"answer":"..."}。'
                            "回答应简洁，引用相关工单或设备编号；不得声称已执行任何生产动作。"
                            "如果问题超出快照数据，明确说明无法确定。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"question": question, "mesSnapshot": facts}, ensure_ascii=False
                        ),
                    },
                ],
                1536,
            )
            answer = _text_field(result, "answer")
            if not answer or len(answer) > 3000:
                raise ValueError("invalid answer length")
            self._record_status("VERIFIED")
            return NaturalLanguageAnswer(answer, self._provider, self._model)
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self._record_status("DEGRADED", exc.__class__.__name__)
            raise ModelGatewayError("model natural language request failed") from exc


class DeepSeekDiagnosticModel(OpenAICompatibleDiagnosticModel):
    """Backward-compatible DeepSeek constructor."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 12.0,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        super().__init__(api_key, model, base_url, timeout_seconds, "DEEPSEEK", egress_policy)


class ConfigurableModelGateway:
    """Hot-swappable gateway. Secrets stay in server memory and are never returned."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 12.0,
        provider: str = "DEEPSEEK",
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        self._lock = Lock()
        self._adapter: OpenAICompatibleDiagnosticModel | None = None
        self._api_key: str | None = None
        self._source = "DISABLED"
        self._egress_policy = egress_policy or EgressPolicy()
        self._blocked_configuration = False
        if api_key or endpoint(base_url) in self._egress_policy.model_local_endpoints:
            try:
                self.configure(provider, model, base_url, timeout_seconds, api_key, "ENVIRONMENT")
            except EgressDenied:
                self._blocked_configuration = True
                self._source = "ENVIRONMENT"

    def configure(
        self,
        provider: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
        api_key: str | None,
        source: str = "RUNTIME",
        verify_connection: bool = False,
    ) -> dict[str, object]:
        self._egress_policy.require_model(base_url)
        base_url = endpoint(base_url)
        with self._lock:
            current = self._adapter
            same_destination = current is not None and (
                current.status()["provider"] == provider.strip().upper()
                and current.status()["baseUrl"] == base_url
            )
            effective_key = api_key or (self._api_key if same_destination else None)
        local = base_url in self._egress_policy.model_local_endpoints
        if api_key is None and current is not None and not local:
            current_status = current.status()
            if (
                str(current_status["provider"]) != provider.strip().upper()
                or str(current_status["baseUrl"]) != base_url.rstrip("/")
            ):
                raise ValueError("a new API key is required when provider or base URL changes")
        if not effective_key and not local:
            raise ValueError("API key is required")
        candidate = OpenAICompatibleDiagnosticModel(
            effective_key or "", model, base_url, timeout_seconds, provider, self._egress_policy
        )
        if verify_connection:
            candidate.answer("只回复连接测试结果。", {"purpose": "CONNECTION_TEST"})
        with self._lock:
            self._adapter = candidate
            self._api_key = effective_key
            self._source = source
            self._blocked_configuration = False
        return self.status()

    def disable(self) -> dict[str, object]:
        with self._lock:
            self._adapter = None
            self._api_key = None
            self._source = "DISABLED"
            self._blocked_configuration = False
        return self.status()

    def status(self) -> dict[str, object]:
        with self._lock:
            adapter = self._adapter
            source = self._source
            blocked = self._blocked_configuration
        if adapter is None:
            return {
                "provider": "NONE",
                "model": None,
                "baseUrl": None,
                "apiKeyConfigured": False,
                "configurationSource": source,
                "connectionStatus": "BLOCKED" if blocked else "DISABLED",
                "networkPolicy": self._egress_policy.status(),
                "lastCheckedAt": None,
                "lastError": "EgressDenied" if blocked else None,
                "lastSuccessAt": None,
                "failureCount": 0,
            }
        return {**adapter.status(), "configurationSource": source,
                "networkPolicy": self._egress_policy.status()}

    def evaluation_adapter(self) -> OpenAICompatibleDiagnosticModel:
        """Capture one adapter so a benchmark cannot mix runtime model configurations."""
        with self._lock:
            adapter = self._adapter
        if adapter is None:
            raise ModelGatewayError("当前模型未配置，无法开始测评。")
        return adapter

    def verify(self) -> dict[str, object]:
        with self._lock:
            adapter = self._adapter
        if adapter is None:
            raise ModelGatewayError("model gateway is disabled")
        adapter.answer("只回复连接测试结果。", {"purpose": "CONNECTION_TEST"})
        return self.status()

    def explain(self, facts: DiagnosticFacts) -> DiagnosticNarrative:
        with self._lock:
            adapter = self._adapter
        if adapter is None:
            raise ModelGatewayError("model gateway is disabled")
        return adapter.explain(facts)

    def answer(self, question: str, facts: dict[str, Any]) -> NaturalLanguageAnswer:
        with self._lock:
            adapter = self._adapter
        if adapter is None:
            raise ModelGatewayError("model gateway is disabled")
        return adapter.answer(question, facts)
