"""评测门禁故障回归；合成案例只测试程序，不用于模型上线验收。"""

import json

import pytest

from src.agents.permission_review_model import ReviewModelResponse
from src.config.model_catalog import PermissionReviewConfig, read_catalog
from src.security.authorization.evaluation import EvaluationBudget, evaluate_dataset, review_identity
from src.security.authorization.evaluation_data import CATEGORIES, EvaluationDataset
from src.security.authorization.review_gate import permission_review_status, require_review_validation
from src.security.authorization.reviewer import freeze_permission_review
from src.storage.permission_evaluation_records import save_evaluation_report
from test.authorization_fakes import run_async


def dataset_payload():
    """明显标明 synthetic-unit-test 来源，绝不持久化到开发用户数据。"""
    categories = sorted(CATEGORIES)
    cases = []
    for i in range(200):
        expected = ("allow", "deny", "needs_human")[i % 3]
        cases.append({"id": f"case-{i}", "groupId": f"source-{i}", "split": "holdout" if i < 42 else "tuning",
                      "source": "synthetic-unit-test", "labeledBy": "unit-test", "labelSource": "human",
                      "categories": [categories[i % len(categories)]], "expected": expected,
                      "critical": expected == "deny", "userGoal": f"案例 {i}", "actorId": "test-actor",
                      "toolName": "NeverExecuted", "effect": "mutation", "actionInputJson": json.dumps({"case": i}),
                      "userRestrictions": ["不得外发"]})
    return {"schemaVersion": 1, "holdoutAttestedBy": "unit-test",
            "holdoutUnusedForTuning": True, "cases": cases}


def binding():
    catalog, _ = read_catalog()
    return freeze_permission_review(catalog=catalog.model_copy(update={
        "permission_review": PermissionReviewConfig(modelConfigId="primary")}))


@pytest.mark.parametrize("failure", ["size", "duplicate", "leak", "coverage", "not_human", "attestation"])
def test_invalid_dataset_cannot_count_as_independent_human_validation(failure):
    payload = dataset_payload()
    if failure == "size":
        payload["cases"].pop()
    elif failure == "duplicate":
        payload["cases"][199] = {**payload["cases"][0], "id": "different-id", "split": "tuning"}
    elif failure == "leak":
        payload["cases"][199]["groupId"] = payload["cases"][0]["groupId"]
    elif failure == "coverage":
        for case in payload["cases"]:
            case["categories"] = ["office"]
    elif failure == "not_human":
        payload["cases"][0]["labelSource"] = "model"
    else:
        payload["holdoutUnusedForTuning"] = False
    with pytest.raises(ValueError):
        EvaluationDataset.model_validate(payload)


@run_async
async def test_isolated_holdout_costs_fault_matrix_and_gate_binding(monkeypatch):
    selected = binding()
    monkeypatch.setattr("src.security.authorization.reviewer.read_catalog", lambda: (selected.model_binding.catalog, "test"))
    dataset = EvaluationDataset.model_validate(dataset_payload())
    calls = []
    mistaken = False

    async def transport(model_binding, *, prompt, **kwargs):
        data = json.loads(prompt)
        i = data["action"]["input"]["case"]
        calls.append(i)
        verdict = "allow" if mistaken else ("allow", "deny", "needs_human")[i % 3]
        code = {"allow": "user_authorized", "deny": "explicit_restriction", "needs_human": "ambiguous_intent"}[verdict]
        return ReviewModelResponse(json.dumps({"verdict": verdict, "reasonCode": code, "reason": "测试理由",
                                               "missingEvidence": []}), 100, 20)

    budget = EvaluationBudget(maxP95Ms=10000.0, inputPricePerMillion=2.0, outputPricePerMillion=4.0, currency="CNY")
    with pytest.raises(ValueError):
        require_review_validation(selected)
    report = await evaluate_dataset(dataset, selected, budget, transport=transport)
    assert calls == list(range(42))
    assert report.passed and all(report.fault_results.values())
    assert report.metrics()["estimatedCost"] == pytest.approx(0.01176)
    assert report.metrics()["structuredSuccessRate"] == 1
    serialized = report.model_dump_json(by_alias=True)
    assert "test-only" not in serialized and "案例" not in serialized and "NeverExecuted" not in serialized
    save_evaluation_report(report.identity, report.model_dump(mode="json", by_alias=True))
    assert require_review_validation(selected).identity == report.identity
    # 无关目录修订不使评测失效，实际模型/配置/连接改变使报告不可复用。
    new_config = selected.config.model_copy(update={"max_output_tokens": 1024})
    assert review_identity(type(selected)(selected.model_binding, new_config)) != report.identity
    with pytest.raises(ValueError):
        require_review_validation(type(selected)(selected.model_binding, new_config))
    assert permission_review_status()["available"] is False  # 当前目录尚未配置审核用途
    monkeypatch.setattr("src.security.authorization.review_gate.freeze_permission_review", lambda: selected)
    enabled = permission_review_status()
    assert enabled["available"] is True and enabled["modelConfigId"] == "primary"
    mistaken = True
    failed = await evaluate_dataset(dataset, selected, budget, transport=transport)
    assert not failed.passed and failed.metrics()["criticalFalseAllowCount"] == 14
    save_evaluation_report(failed.identity, failed.model_dump(mode="json", by_alias=True))
    with pytest.raises(ValueError):
        require_review_validation(selected)


@run_async
async def test_observer_failure_never_changes_or_retries_model_decision():
    from src.security.authorization.review_contract import ReviewEvidence
    from src.security.authorization.reviewer import PermissionReviewer
    from test.test_permission_records import action_for

    calls = 0

    async def evidence(action):
        return ReviewEvidence("改写报告", '{"content":"新报告"}')

    async def transport(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ReviewModelResponse('{"verdict":"allow","reasonCode":"user_authorized","reason":"符合目标","missingEvidence":[]}')

    def broken_observer(record):
        raise RuntimeError("观测不可用")

    reviewer = PermissionReviewer(binding=binding(), evidence=evidence, transport=transport,
                                  available=lambda _: True, observe=broken_observer)
    assert (await reviewer(action_for(approvalMode="model_review"))).verdict == "allow"
    assert calls == 1


@run_async
async def test_rejected_review_connection_is_not_reported_as_bad_format():
    from src.security.authorization.review_contract import ReviewEvidence
    from src.security.authorization.reviewer import PermissionReviewer
    from test.test_permission_records import action_for

    async def evidence(action):
        return ReviewEvidence("改写报告", '{"content":"新报告"}')

    async def transport(*args, **kwargs):
        error = RuntimeError("subscription expired")
        error.status_code = 403
        raise error

    reviewer = PermissionReviewer(binding=binding(), evidence=evidence, transport=transport, available=lambda _: True)
    decision = await reviewer(action_for(approvalMode="model_review"))
    assert decision.verdict == "needs_human"
    assert decision.reason_code == "review_unavailable"
    assert "额度" in decision.reason
