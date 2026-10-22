#!/usr/bin/env python3
"""yinli/ AGENTS.md 检查脚本的单测。只使用标准库。"""

from __future__ import annotations

import unittest
from pathlib import Path

import check_agents_md as checker

SHIM = (
    "# CLAUDE.md\n\n"
    "The yinli agent guidance lives in [AGENTS.md](AGENTS.md) "
    "so it is shared across coding agents (Claude Code, Codex, and others). "
    "Claude Code imports it below.\n\n"
    "@AGENTS.md\n"
)
ROOT = "# yinli\n\nScope.\n"
CHILD = "# agent\n\n- Keep construction here.\n"


def exists_from(files: dict[str, str]):
    def exists(path: str) -> bool:
        return path in files

    return exists


class CheckAgentsMdTest(unittest.TestCase):
    def test_small_tree_passes(self) -> None:
        files = {
            "yinli/AGENTS.md": ROOT,
            "yinli/CLAUDE.md": SHIM,
            "yinli/agent/AGENTS.md": CHILD + "Parent: [../AGENTS.md](../AGENTS.md).\n",
        }
        findings = checker.analyze(files, {}, set(files), exists_from(files))
        self.assertEqual(findings, [])

    def test_hard_limit_growth_is_error(self) -> None:
        bloated = "# yinli\n\n" + ("x" * 21000)
        files = {"yinli/AGENTS.md": bloated, "yinli/CLAUDE.md": SHIM}
        findings = checker.analyze(
            files,
            {"yinli/AGENTS.md": 100},
            {"yinli/AGENTS.md"},
            exists_from(files),
        )
        self.assertTrue(any(item.code == "AM001" and item.severity == "error" for item in findings))

    def test_hard_limit_without_growth_is_warning(self) -> None:
        bloated = "# yinli\n\n" + ("x" * 21000)
        files = {"yinli/AGENTS.md": bloated, "yinli/CLAUDE.md": SHIM}
        size = checker.utf8_size(bloated)
        findings = checker.analyze(
            files,
            {"yinli/AGENTS.md": size},
            set(),
            exists_from(files),
        )
        matched = [item for item in findings if item.code == "AM001"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].severity, "warning")

    def test_soft_limit_warns_only_when_changed(self) -> None:
        text = "# yinli\n\n" + ("x" * 17000)
        files = {"yinli/AGENTS.md": text, "yinli/CLAUDE.md": SHIM}
        quiet = checker.analyze(files, {}, set(), exists_from(files))
        noisy = checker.analyze(files, {}, {"yinli/AGENTS.md"}, exists_from(files))
        self.assertFalse(any(item.code == "AM001" for item in quiet))
        self.assertTrue(any(item.code == "AM001" and item.severity == "warning" for item in noisy))

    def test_broken_link_is_error(self) -> None:
        files = {
            "yinli/AGENTS.md": ROOT + "See [missing](missing.md).\n",
            "yinli/CLAUDE.md": SHIM,
        }
        findings = checker.analyze(files, {}, set(), exists_from(files))
        self.assertTrue(any(item.code == "AM003" and item.severity == "error" for item in findings))

    def test_http_links_are_ignored(self) -> None:
        files = {
            "yinli/AGENTS.md": ROOT + "See [docs](https://example.com/doc).\n",
            "yinli/CLAUDE.md": SHIM,
        }
        findings = checker.analyze(files, {}, set(), exists_from(files))
        self.assertFalse(any(item.code == "AM003" for item in findings))

    def test_fenced_links_are_ignored(self) -> None:
        files = {
            "yinli/AGENTS.md": ROOT + "```\n[missing](missing.md)\n```\n",
            "yinli/CLAUDE.md": SHIM,
        }
        findings = checker.analyze(files, {}, set(), exists_from(files))
        self.assertFalse(any(item.code == "AM003" for item in findings))

    def test_missing_heading_warns(self) -> None:
        files = {"yinli/AGENTS.md": "no heading\n", "yinli/CLAUDE.md": SHIM}
        findings = checker.analyze(files, {}, set(), exists_from(files))
        self.assertTrue(any(item.code == "AM004" for item in findings))

    def test_bad_shim_and_orphan_claude(self) -> None:
        files = {"yinli/agent/CLAUDE.md": "# CLAUDE.md\n\nextra rules\n"}
        findings = checker.analyze(files, {}, set(), exists_from(files))
        codes = {item.code for item in findings}
        self.assertIn("AM005", codes)
        self.assertIn("AM006", codes)

    def test_missing_root_shim(self) -> None:
        files = {"yinli/AGENTS.md": ROOT}
        findings = checker.analyze(files, {}, set(), exists_from(files))
        self.assertTrue(any(item.code == "AM005" and item.path == "yinli/CLAUDE.md" for item in findings))

    def test_chain_excludes_repository_root(self) -> None:
        self.assertEqual(
            checker.chain_keys("yinli/agent/AGENTS.md"),
            ["yinli/AGENTS.md", "yinli/agent/AGENTS.md"],
        )

    def test_newline_normalization(self) -> None:
        self.assertEqual(checker.utf8_size("a\r\nb"), checker.utf8_size("a\nb"))

    def test_files_outside_yinli_are_ignored(self) -> None:
        files = {
            "AGENTS.md": "x" * 30000,
            "yinli/AGENTS.md": ROOT,
            "yinli/CLAUDE.md": SHIM,
        }
        findings = checker.analyze(files, {}, set(), exists_from(files))
        self.assertEqual(findings, [])

    def test_live_yinli_tree(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        code = checker.main(["--repo", str(repo)])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
