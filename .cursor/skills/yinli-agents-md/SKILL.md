---
name: yinli-agents-md
description: >-
  Write and check layered AGENTS.md files under yinli/ using the project
  guidance spec. Use when adding or editing yinli/AGENTS.md, any
  yinli/**/AGENTS.md, yinli/CLAUDE.md, or when changing yinli module
  boundaries, public exports, commands, config keys, or directory layout.
  The repository root, frontend/, and backend/ use the same wording.
  The byte checker still scans only yinli/.
---

# yinli AGENTS.md

Inside `yinli/`, edit only `yinli/**/AGENTS.md` and the `yinli/CLAUDE.md` shim.

The running app has its own tree. Shared rules stay in the repository root `AGENTS.md`. Frontend rules stay in `frontend/AGENTS.md` and the feature files it links. Backend rules stay in `backend/AGENTS.md` and the package files it links. Write the body in Simplified Chinese, one rule per line. Link upward. Do not copy a parent rule down.

## When a change needs an update

Update the nearest `AGENTS.md` in the same change when you change any of these:

- capability ownership or a public export
- import direction
- commands
- config keys or environment variables
- directory add, rename, or delete

Under `yinli/`, the nearest file is `yinli/**/AGENTS.md`. Under the running app, it is the repository root, `frontend/`, or `backend/` file closest to the edit. Leave the file alone when the change does not touch those.

## Write

1. Read [reference.md](reference.md) for the layer you are editing.
2. Edit the nearest file only. Link upward. Do not copy parent rules downward.
3. Keep `yinli/CLAUDE.md` as the fixed shim in reference.md. Do not add rules to it. Do not add `CLAUDE.md` under a capability directory.
4. Write the `AGENTS.md` body in Simplified Chinese. One rule per line. Imperative. Keep identifiers, paths, and commands in English inside backticks.
5. Write only facts. Mark an unknown with `TODO(agents-md):`. Do not invent commands, tests, or import contracts.

## Check

From the repository root, after every guidance edit:

```bash
python scripts/check_agents_md.py
python scripts/test_check_agents_md.py
```

Fix every error before stopping. The checker looks only at `yinli/`. `AM001` and `AM002` are errors only when a file or ancestor chain is over its hard byte limit and larger than the baseline. `AM003` and `AM005` and `AM006` are always errors. `AM004` is a warning.
