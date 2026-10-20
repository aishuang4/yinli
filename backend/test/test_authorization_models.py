"""验证授权合同严格性、版本和动作快照不可变性。"""

import pytest
from pydantic import ValidationError

from src.runtime.authorization import ActionSnapshot, RunAuthorization, TargetVersion


def make_action(**updates):
    """使用全虚构归属与输入构造动作。"""

    return ActionSnapshot(
        authorization=RunAuthorization(tenantId="local", workspaceId="w", projectId="p",
                                       sessionId="s", runId="r", actorId="a", capabilityVersion="v1"),
        callId="c", toolName="Fake", toolVersion="1", normalizedInputJson='{"b":2,"a":1}',
        **updates,
    )


@pytest.mark.parametrize("mode", ["manual", "model_review", "full_access"])
def test_three_modes_round_trip(mode):
    action = make_action()
    data = action.authorization.model_dump(by_alias=True)
    data["approvalMode"] = mode
    policy = RunAuthorization.model_validate(data)
    assert policy.approval_mode == mode
    assert "approval_mode" not in policy.model_dump(by_alias=True)


def test_versions_strict_types_and_unknown_fields():
    action = make_action()
    for updates in ({"schemaVersion": 2}, {"schemaVersion": "1"}, {"confidence": 0.9}):
        data = action.model_dump(by_alias=True)
        data.update(updates)
        with pytest.raises(ValidationError):
            ActionSnapshot.model_validate(data)
    data = action.authorization.model_dump(by_alias=True)
    for value in ("bypass", True, 1):
        with pytest.raises(ValidationError):
            RunAuthorization.model_validate({**data, "approvalMode": value})


def test_input_is_canonical_and_snapshot_is_frozen():
    action = make_action()
    assert action.normalized_input_json == '{"a":1,"b":2}'
    data = action.model_dump(by_alias=True)
    data["normalizedInputJson"] = '{ "a":1, "b":2 }'
    assert ActionSnapshot.model_validate(data).fingerprint == action.fingerprint
    with pytest.raises(ValidationError):
        action.effect = "read_only"
    assert make_action(targets=(TargetVersion(target="/fake/a", version="v1"),)).fingerprint != action.fingerprint


@pytest.mark.parametrize("value", ['[]', '{"a":NaN}', '{"a":1,"a":2}', '{bad}'])
def test_invalid_json_is_not_repaired(value):
    data = make_action().model_dump(by_alias=True)
    data["normalizedInputJson"] = value
    with pytest.raises(ValidationError):
        ActionSnapshot.model_validate(data)
