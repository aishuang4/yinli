#!/usr/bin/env python3
"""检查 yinli/ 分层 AGENTS.md 的体积、垫片和相对链接。

范围固定为 yinli/。不读取、不评价仓库根目录或其他目录的 AGENTS.md。
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

SCOPE = "yinli"
ROOT_SOFT = 16 * 1024
ROOT_HARD = 20 * 1024
MID_SOFT = 28 * 1024
MID_HARD = 32 * 1024
DEEP_SOFT = 40 * 1024
DEEP_HARD = 48 * 1024
CHAIN_SOFT = 80 * 1024
CHAIN_HARD = 96 * 1024
ZERO_REV = "0" * 40

SHIM_RE = re.compile(
    r"# CLAUDE\.md\n\n"
    r"The .+ agent guidance lives in \[AGENTS\.md\]\(AGENTS\.md\) "
    r"so it is shared across coding agents \(Claude Code, Codex, and others\)\. "
    r"Claude Code imports it below\.\n\n"
    r"@AGENTS\.md\n?\Z"
)
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    path: str
    message: str


def utf8_size(text: str) -> int:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return len(normalized.encode("utf-8"))


def file_budget(scope_rel: str) -> tuple[int, int]:
    depth = len(PurePosixPath(scope_rel).parts)
    if depth <= 1:
        return ROOT_SOFT, ROOT_HARD
    if depth == 2:
        return MID_SOFT, MID_HARD
    return DEEP_SOFT, DEEP_HARD


def scope_relative(path: str) -> str:
    prefix = SCOPE + "/"
    if not path.startswith(prefix):
        raise ValueError(path)
    return path[len(prefix) :]


def is_agents(path: str) -> bool:
    return PurePosixPath(path).name == "AGENTS.md"


def is_claude(path: str) -> bool:
    return PurePosixPath(path).name == "CLAUDE.md"


def in_scope(path: str) -> bool:
    return path == SCOPE or path.startswith(SCOPE + "/")


def chain_keys(agents_path: str) -> list[str]:
    relative = PurePosixPath(scope_relative(agents_path))
    keys = [f"{SCOPE}/AGENTS.md"]
    cursor = PurePosixPath(SCOPE)
    for part in relative.parts[:-1]:
        cursor = cursor / part
        keys.append(f"{cursor.as_posix()}/AGENTS.md")
    if keys[-1] != agents_path:
        keys.append(agents_path)
    return keys


def strip_fences(text: str) -> str:
    return FENCE_RE.sub("", text)


def link_targets(from_file: str, text: str) -> list[str]:
    targets: list[str] = []
    for match in LINK_RE.finditer(strip_fences(text)):
        dest = unquote(match.group(1))
        if dest.startswith(("http://", "https://", "mailto:", "//")):
            continue
        dest = dest.split("#", 1)[0].split("?", 1)[0].strip()
        if not dest:
            continue
        if dest.startswith("/"):
            resolved = posixpath.normpath(dest.lstrip("/"))
        else:
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(from_file), dest)
            )
        targets.append(resolved)
    return targets


def is_shim(text: str) -> bool:
    return SHIM_RE.fullmatch(text.replace("\r\n", "\n").replace("\r", "\n")) is not None


def first_line_is_heading(text: str) -> bool:
    first = text.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0]
    return first.startswith("# ")


def analyze(
    files: dict[str, str],
    baseline_sizes: dict[str, int],
    changed: set[str],
    exists,
) -> list[Finding]:
    scoped = {path: text for path, text in files.items() if in_scope(path)}
    findings: list[Finding] = []
    sizes = {path: utf8_size(text) for path, text in scoped.items() if is_agents(path)}

    if f"{SCOPE}/AGENTS.md" in scoped and f"{SCOPE}/CLAUDE.md" not in scoped:
        findings.append(
            Finding(
                "AM005",
                "error",
                f"{SCOPE}/CLAUDE.md",
                "yinli/ 缺少 CLAUDE.md 垫片",
            )
        )

    for path, text in sorted(scoped.items()):
        if is_agents(path):
            soft, hard = file_budget(scope_relative(path))
            size = sizes[path]
            base = baseline_sizes.get(path, 0)
            grew = size > base
            if size > hard and grew:
                findings.append(
                    Finding("AM001", "error", path, f"{size} 字节，超过硬上限 {hard} 并且比基线 {base} 变大")
                )
            elif size > hard:
                findings.append(
                    Finding("AM001", "warning", path, f"{size} 字节，超过硬上限 {hard}，但没有变大")
                )
            elif size > soft and path in changed:
                findings.append(
                    Finding("AM001", "warning", path, f"{size} 字节，超过软上限 {soft}")
                )
            if not first_line_is_heading(text):
                findings.append(Finding("AM004", "warning", path, "首行不是一级标题"))
            for target in link_targets(path, text):
                if target.startswith("../") or not exists(target):
                    findings.append(Finding("AM003", "error", path, f"相对链接失效: {target}"))

        if is_claude(path):
            if not is_shim(text):
                findings.append(Finding("AM005", "error", path, "CLAUDE.md 不是垫片"))
            sibling = str(PurePosixPath(path).with_name("AGENTS.md"))
            if sibling not in scoped:
                findings.append(Finding("AM006", "error", path, "同目录没有 AGENTS.md"))
            for target in link_targets(path, text):
                if target.startswith("../") or not exists(target):
                    findings.append(Finding("AM003", "error", path, f"相对链接失效: {target}"))

    for path in sorted(sizes):
        keys = [key for key in chain_keys(path) if key in sizes or baseline_sizes.get(key, 0)]
        current = sum(sizes.get(key, 0) for key in keys)
        base = sum(baseline_sizes.get(key, 0) for key in keys)
        grew = current > base
        touched = any(key in changed for key in keys)
        if current > CHAIN_HARD and grew:
            findings.append(
                Finding(
                    "AM002",
                    "error",
                    path,
                    f"祖先链 {current} 字节，超过硬上限 {CHAIN_HARD} 并且比基线 {base} 变大",
                )
            )
        elif current > CHAIN_HARD:
            findings.append(
                Finding("AM002", "warning", path, f"祖先链 {current} 字节，超过硬上限 {CHAIN_HARD}，但没有变大")
            )
        elif current > CHAIN_SOFT and touched:
            findings.append(
                Finding("AM002", "warning", path, f"祖先链 {current} 字节，超过软上限 {CHAIN_SOFT}")
            )
    return findings


def git_output(repo: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, cwd=repo, capture_output=True, check=False)


def rev_exists(repo: Path, rev: str) -> bool:
    if not rev or rev == ZERO_REV:
        return False
    completed = git_output(repo, ["git", "rev-parse", "--verify", "--quiet", rev])
    return completed.returncode == 0


def list_scope_files(repo: Path) -> list[str]:
    completed = git_output(
        repo,
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", SCOPE],
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.decode() or "无法列出 yinli/ 文件")
    names = [item.decode() for item in completed.stdout.split(b"\0") if item]
    return sorted(
        name
        for name in names
        if in_scope(name) and (is_agents(name) or is_claude(name))
    )


def read_worktree(repo: Path, rel: str) -> str | None:
    path = repo / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def read_rev(repo: Path, rev: str, rel: str) -> str | None:
    completed = git_output(repo, ["git", "show", f"{rev}:{rel}"])
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8")


def load_files(repo: Path, rev: str | None, names: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for name in names:
        text = read_rev(repo, rev, name) if rev else read_worktree(repo, name)
        if text is not None:
            files[name] = text
    return files


def baseline_sizes_for(repo: Path, rev: str | None, names: list[str]) -> dict[str, int]:
    if rev is None or not rev_exists(repo, rev):
        return {}
    sizes: dict[str, int] = {}
    for name in names:
        text = read_rev(repo, rev, name)
        if text is not None and is_agents(name):
            sizes[name] = utf8_size(text)
    return sizes


def changed_paths(current: dict[str, str], repo: Path, rev: str | None, names: list[str]) -> set[str]:
    if rev is None or not rev_exists(repo, rev):
        return set(current)
    changed: set[str] = set()
    for name in names:
        before = read_rev(repo, rev, name)
        after = current.get(name)
        if before != after:
            changed.add(name)
    return changed


def repo_root(start: Path) -> Path:
    completed = git_output(start, ["git", "rev-parse", "--show-toplevel"])
    if completed.returncode != 0:
        raise SystemExit("必须在 git 仓库内运行")
    return Path(completed.stdout.decode().strip())


def format_finding(finding: Finding, annotations: bool) -> str:
    text = f"{finding.code} {finding.severity} {finding.path}: {finding.message}"
    if not annotations:
        return text
    level = "error" if finding.severity == "error" else "warning"
    escaped = finding.message.replace("\n", " ")
    return f"::{level} file={finding.path},line=1,title={finding.code}::{escaped}\n{text}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查 yinli/ 的 AGENTS.md 与 CLAUDE.md")
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--base-ref", default=None)
    parser.add_argument("--head-ref", default=None)
    parser.add_argument("--before", default=None)
    parser.add_argument("--after", default=None)
    parser.add_argument("--github-annotations", action="store_true")
    parser.add_argument("--strict-warnings", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.before and args.base_ref:
        raise SystemExit("不要同时传入 --before 和 --base-ref")
    repo = (args.repo or Path.cwd()).resolve()
    if args.repo is None:
        repo = repo_root(repo)
    elif not (repo / ".git").exists() and git_output(repo, ["git", "rev-parse", "--is-inside-work-tree"]).returncode != 0:
        raise SystemExit(f"不是 git 仓库: {repo}")

    current_rev = args.after or args.head_ref
    baseline_rev = args.before if args.after or args.before else (args.base_ref or "HEAD")
    if current_rev and not rev_exists(repo, current_rev):
        raise SystemExit(f"找不到版本 {current_rev}")
    if baseline_rev and baseline_rev != "HEAD" and not rev_exists(repo, baseline_rev):
        raise SystemExit(f"找不到基线 {baseline_rev}")

    names = list_scope_files(repo)
    if current_rev:
        listed = set(names)
        completed = git_output(repo, ["git", "ls-tree", "-r", "--name-only", current_rev, SCOPE])
        if completed.returncode == 0:
            for line in completed.stdout.decode().splitlines():
                if is_agents(line) or is_claude(line):
                    listed.add(line)
        names = sorted(listed)
    current = load_files(repo, current_rev, names)
    baseline = baseline_sizes_for(repo, baseline_rev if rev_exists(repo, baseline_rev) else None, names)
    changed = changed_paths(
        current,
        repo,
        baseline_rev if rev_exists(repo, baseline_rev) else None,
        names,
    )

    def exists(path: str) -> bool:
        if path in current:
            return True
        if current_rev:
            return git_output(repo, ["git", "cat-file", "-e", f"{current_rev}:{path}"]).returncode == 0
        return (repo / path).is_file()

    findings = analyze(current, baseline, changed, exists)
    errors = 0
    for finding in findings:
        severity = finding.severity
        if args.strict_warnings and severity == "warning":
            severity = "error"
            finding = Finding(finding.code, severity, finding.path, finding.message)
        if finding.severity == "error":
            errors += 1
        print(format_finding(finding, args.github_annotations))
    if errors:
        print(f"{errors} error(s)", file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
