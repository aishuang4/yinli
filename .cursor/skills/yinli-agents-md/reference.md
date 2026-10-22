# yinli guidance reference

Scope is `yinli/` only. `yinli/AGENTS.md` is the root of this tree. Capability directories are leaves.

## Where a file goes

- `yinli/AGENTS.md` is required.
- `yinli/CLAUDE.md` is required and is only a shim.
- A capability `AGENTS.md` is required for every directory that already has one, and for a new directory that owns a boundary.
- Do not add an `AGENTS.md` to a directory of a few obvious helpers.
- Do not add `CLAUDE.md` inside a capability directory.

## Shim

`yinli/CLAUDE.md` must be exactly:

```markdown
# CLAUDE.md

The yinli agent guidance lives in [AGENTS.md](AGENTS.md) so it is shared across coding agents (Claude Code, Codex, and others). Claude Code imports it below.

@AGENTS.md
```

## Root sections

`yinli/AGENTS.md` keeps these sections: Purpose, Layout, Boundaries, Commands, Testing, Keep in sync.

- Purpose: what this tree owns.
- Layout: one linked line per capability.
- Boundaries: who may import whom. State `TODO(agents-md)` until an import check exists.
- Commands: only commands that exist. The guidance check is `python scripts/check_agents_md.py`.
- Testing: where tests live. Say none if there are none.
- Keep in sync: update this file when directories change, and the nearest child when its export changes.

## Leaf

Start with `# <directory>`, one sentence, and a link to [../AGENTS.md](../AGENTS.md). Then one line each for entry, invariant, config, test, and sync. Delete a line that has no fact.

## Budgets

Bytes are UTF-8 after newlines are normalized to `\n`.

| File | Soft | Hard |
| --- | --- | --- |
| `yinli/AGENTS.md` | 16KB | 20KB |
| `yinli/<capability>/AGENTS.md` | 28KB | 32KB |
| deeper | 40KB | 48KB |
| chain inside `yinli/` | 80KB | 96KB |

The chain is `yinli/AGENTS.md` plus the child. It does not include the repository root `AGENTS.md`.

Split a file before it reaches the soft limit. Move the detail into the child and leave one sentence plus a link.

## Wording

- Simplified Chinese, imperative, one rule per line. Keep identifiers, paths, and commands in English inside backticks.
- Give the path or the check name when there is one.
- Do not write versions, line numbers, personal names, or status that will be stale next week.
- Do not repeat a parent rule. Link to it.
