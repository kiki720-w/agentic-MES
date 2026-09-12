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
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._provider = provider.strip().upper()
        self._status_lock = Lock()
        self._connection_status = "NOT_TESTED"
        self._last_checked_at: str | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, object]:
        with self._status_lock:
            return {
                "provider": self._provider,
                "model": self._model,
                "baseUrl": self._base_url,
                "apiKeyConfigured": True,
                "connectionStatus": self._connection_status,
                "lastCheckedAt": self._last_checked_at,
                "lastError": self._last_error,
            }

    def _record_status(self, status: str, error: str | None = None) -> None:
        with self._status_lock:
            self._connection_status = status
            self._last_checked_at = datetime.now(UTC).isoformat()
            self._last_error = error

    def _request(self, messages: list[dict[str, str]], max_tokens: int) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
            timeout=self._timeout,
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        content = body["choices"][0]["message"]["content"]
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
                320,
            )
            diagnosis = str(result["diagnosis"]).strip()
            recommendation = str(result["recommendation"]).strip()
            if (
                not diagnosis
                or not recommendation
                or len(diagnosis) > 800
                or len(recommendation) > 800
            ):
                raise ValueError("invalid narrative length")
            self._record_status("CONNECTED")
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
                700,
            )
            answer = str(result["answer"]).strip()
            if not answer or len(answer) > 3000:
                raise ValueError("invalid answer length")
            self._record_status("CONNECTED")
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
    ) -> None:
        super().__init__(api_key, model, base_url, timeout_seconds, "DEEPSEEK")


class ConfigurableModelGateway:
    """Hot-swappable gateway. Secrets stay in server memory and are never returned."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 12.0,
        provider: str = "DEEPSEEK",
    ) -> None:
        self._lock = Lock()
        self._adapter: OpenAICompatibleDiagnosticModel | None = None
        self._api_key: str | None = None
        self._source = "DISABLED"
        if api_key:
            self.configure(provider, model, base_url, timeout_seconds, api_key, "ENVIRONMENT")

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
        with self._lock:
            current = self._adapter
            effective_key = api_key or self._api_key
        if api_key is None and current is not None:
            current_status = current.status()
            if (
                str(current_status["provider"]) != provider.strip().upper()
                or str(current_status["baseUrl"]) != base_url.rstrip("/")
            ):
                raise ValueError("a new API key is required when provider or base URL changes")
        if not effective_key:
            raise ValueError("API key is required")
        candidate = OpenAICompatibleDiagnosticModel(
            effective_key, model, base_url, timeout_seconds, provider
        )
        if verify_connection:
            candidate.answer("只回复连接测试结果。", {"purpose": "CONNECTION_TEST"})
        with self._lock:
            self._adapter = candidate
            self._api_key = effective_key
            self._source = source
        return self.status()

    def disable(self) -> dict[str, object]:
        with self._lock:
            self._adapter = None
            self._api_key = None
            self._source = "DISABLED"
        return self.status()

    def status(self) -> dict[str, object]:
        with self._lock:
            adapter = self._adapter
            source = self._source
        if adapter is None:
            return {
                "provider": "NONE",
                "model": None,
                "baseUrl": None,
                "apiKeyConfigured": False,
                "configurationSource": source,
                "connectionStatus": "DISABLED",
                "lastCheckedAt": None,
                "lastError": None,
            }
        return {**adapter.status(), "configurationSource": source}

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
