"""Small, reproducible text smoke benchmark; never reads factory data or writes production."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from autonomous_mes.application.model_gateway import ModelGatewayError, NaturalLanguageModel

CASES: tuple[tuple[str, str, dict[str, Any], str], ...] = (
    ("fact-reference", "仅回答工单编号和数量，格式：编号|数量。",
     {"workOrders": [{"code": "WO-CHECK-001", "quantity": 12}]}, "WO-CHECK-001|12"),
    ("missing-fact", "WO-CHECK-999 的交期是什么？缺少事实时仅回答：无法确定。",
     {"workOrders": []}, "无法确定"),
    ("arithmetic", "准备分钟加数量乘单件分钟是多少？仅回答数字。",
     {"setupMinutes": 20, "quantity": 12, "minutesPerUnit": 5}, "80"),
)


def run_text_check(
    model: NaturalLanguageModel, actor: str, status: dict[str, object],
    report_directory: Path,
) -> dict[str, Any]:
    checks = []
    for case_id, question, facts, expected in CASES:
        started = perf_counter()
        answer_hash = None
        source = "UNAVAILABLE"
        model_name = None
        error = None
        passed = False
        try:
            result = model.answer(question, facts)
            source = result.source
            model_name = result.model
            answer_hash = hashlib.sha256(result.answer.encode()).hexdigest()
            passed = (source not in {"RULES", "POLICY"}
                      and result.answer.strip().rstrip("。.") == expected)
        except ModelGatewayError:
            error = "MODEL_REQUEST_FAILED"
        checks.append({"id": case_id, "passed": passed, "expected": expected,
                       "source": source, "model": model_name, "answerSha256": answer_hash, "error": error,
                       "latencyMs": round((perf_counter() - started) * 1000)})
    report = {
        "runId": str(uuid4()), "suiteVersion": "text-smoke-v1",
        "createdAt": datetime.now(UTC).isoformat(), "actor": actor,
        "provider": status.get("provider"), "model": status.get("model"),
        "fixtureSha256": hashlib.sha256(json.dumps(CASES, ensure_ascii=False).encode()).hexdigest(),
        "dataScope": "SYNTHETIC_ONLY", "checks": checks,
        "passed": all(check["passed"] for check in checks),
        "limitations": "3 exact-match smoke cases; not production accuracy certification. "
                        "PDF/image understanding, multi-turn, cost and zero-egress are not verified.",
    }
    report_directory.mkdir(parents=True, exist_ok=True)
    target = report_directory / f"{report['runId']}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
