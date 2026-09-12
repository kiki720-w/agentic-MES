import json
from datetime import UTC, datetime
from threading import Lock
from typing import Any

import httpx

from autonomous_mes.application.model_gateway import (
    DiagnosticFacts,
    DiagnosticNarrative,
    ModelGatewayError,
)


class DeepSeekDiagnosticModel:
    """Explanation-only adapter: it receives facts and exposes no MES tools."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 12.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._status_lock = Lock()
        self._connection_status = "NOT_TESTED"
        self._last_checked_at: str | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, str | None]:
        with self._status_lock:
            return {
                "provider": "DEEPSEEK",
                "model": self._model,
                "connectionStatus": self._connection_status,
                "lastCheckedAt": self._last_checked_at,
                "lastError": self._last_error,
            }

    def _record_status(self, status: str, error: str | None = None) -> None:
        with self._status_lock:
            self._connection_status = status
            self._last_checked_at = datetime.now(UTC).isoformat()
            self._last_error = error

    def explain(self, facts: DiagnosticFacts) -> DiagnosticNarrative:
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是机械加工MES诊断解释器。只依据用户提供的JSON事实输出JSON，"
                        "格式为{\"diagnosis\":\"...\",\"recommendation\":\"...\"}。"
                        "不得声称已执行动作，不得要求修改数据库、绕过审批或控制设备。"
                        "建议必须说明由有权限人员确认后执行。"
                    ),
                },
                {"role": "user", "content": json.dumps(facts.__dict__, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": 320,
            "temperature": 0.1,
        }
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            content = body["choices"][0]["message"]["content"]
            result = json.loads(content)
            diagnosis = str(result["diagnosis"]).strip()
            recommendation = str(result["recommendation"]).strip()
            if not diagnosis or not recommendation or len(diagnosis) > 800 or len(recommendation) > 800:
                raise ValueError("invalid narrative length")
            self._record_status("CONNECTED")
            return DiagnosticNarrative(diagnosis, recommendation, "DEEPSEEK", self._model)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._record_status("DEGRADED", exc.__class__.__name__)
            raise ModelGatewayError("DeepSeek diagnostic request failed") from exc
