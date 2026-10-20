"""可信身份及保守分类回归，避免把外发、脚本或 MCP 自述当只读。"""

import pytest

from src.security.authorization.effects import ToolIdentity, classify_effect, is_simple_readonly_shell


@pytest.mark.parametrize("module,cls,effect", [
    ("scoped_read", "ScopedRead", "read_only"), ("scoped_grep", "ScopedGrep", "read_only"),
    ("scoped_glob", "ScopedGlob", "read_only"), ("scoped_write", "ScopedWrite", "mutation"),
    ("scoped_edit", "ScopedEdit", "mutation"), ("timeline_memory", "TimelineIngest", "mutation"),
    ("scheduled_job_tools", "ScheduleJob", "mutation"), ("delegate_task", "DelegateTask", "orchestration"),
])
def test_registered_effects(module, cls, effect):
    assert classify_effect(ToolIdentity(f"src.tools.{module}", cls, "anything"), {}) == effect


def test_untrusted_tool_name_annotation_and_get_are_not_readonly():
    assert classify_effect(ToolIdentity("untrusted", "Fake", "Read"), {"readonly": True}) == "unknown"
    assert classify_effect(ToolIdentity("src.tools.scoped_read", "ScopedRead", "Read", True), {}) == "unknown"
    for cls, module in [("HttpRequest", "http_request"), ("ConfiguredHttpTool", "configured_http_tool")]:
        assert classify_effect(ToolIdentity(f"src.tools.{module}", cls, cls),
                               {"method": "GET", "readonly": True, "query": {"secret": "x"}}) == "outbound"


@pytest.mark.parametrize("command", ["pytest", "npm test", "python script.py", "git status", "pwd > out",
    "pwd; touch out", "cat $(pwd)", "cat `pwd`", "cat $FILE", "cat file | wc -l", "cat *.txt",
    "cat --help", "find . -exec rm {} ;", "sed -i s/a/b/ file", "cat a\nrm b"])
def test_unregistered_shell_is_unknown(command):
    assert not is_simple_readonly_shell(command)


@pytest.mark.parametrize("command", ["pwd", "cat file.txt", 'cat "a b.txt"', "wc -l file.txt"])
def test_simple_templates(command):
    assert is_simple_readonly_shell(command)


def test_webfetch_only_known_plain_url_is_readonly():
    identity = ToolIdentity("src.tools.web_fetch", "WebFetch", "WebFetch")
    url = "https://example.org/page"
    assert classify_effect(identity, {"url": url}, known_urls=frozenset({url})) == "read_only"
    assert classify_effect(identity, {"url": url}) == "outbound"
    for unsafe in (url + "?data=private", "https://user:password@example.org/page"):
        assert classify_effect(identity, {"url": unsafe}, known_urls=frozenset({unsafe})) == "outbound"


@pytest.mark.parametrize("cls", ["MemoryAdd", "MemoryUpdate", "MemoryDelete", "SkillDraftPropose"])
def test_internal_memory_mutations_are_not_framework_readonly(cls):
    assert classify_effect(ToolIdentity("src.tools.memory_reflection", cls, "read"), {}) == "mutation"


@pytest.mark.parametrize("cls", ["SubagentTaskWait", "SubagentResultRead", "SubagentTaskCancel"])
def test_child_delivery_and_cancel_have_state_effects(cls):
    assert classify_effect(ToolIdentity("src.tools.delegate_task", cls, "read"), {}) == "mutation"


def test_team_read_mailbox_acknowledges_and_unknown_classes_stay_unknown():
    module = "src.server.routes.chat.team_runtime.tools"
    assert classify_effect(ToolIdentity(module, "ReadMailbox", "read"), {}) == "mutation"
    assert classify_effect(ToolIdentity(module, "FutureTool", "team_read_tasks"), {}) == "unknown"
    assert classify_effect(ToolIdentity(module, "ReadTasks", "anything"), {}) == "read_only"
    assert classify_effect(ToolIdentity("src.server.routes.chat.plan_mode.tools", "TaskListTool", "list"), {}) == "read_only"


def test_shell_syntax_without_resolved_execution_proof_never_exempts():
    identity = ToolIdentity("src.agents.guarded_bash", "GuardedBash", "Bash")
    assert classify_effect(identity, {"command": "cat file.txt"}) == "unknown"
