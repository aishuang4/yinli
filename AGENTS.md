# Repository Guidelines

## Project Structure & Module Organization

- `backend/src/`: FastAPI service, Agent runtime, routes, storage, and tools.
- `backend/test/` and `backend/tests/`: backend test locations.
- `frontend/src/`: React/Vite UI, pages, hooks, stores, types, and themes.
- `docs/`: product specs, implementation plans, TODOs, and handoff notes.
- `electron/`, `docker/`, `scripts/`: desktop, container, and helper tooling.
- `backend/data/`: local development SQLite data and generated runtime files. Treat as local state.

## Build, Test, and Development Commands

Backend:

```bash
cd backend
uv run uvicorn src.server.main:app --host 0.0.0.0 --port 8765 --reload
.venv/bin/python -m py_compile $(find src -name '*.py' -print)
```

The first command starts the API with hot reload; the second catches Python syntax errors.

Frontend:

```bash
cd frontend
npm run dev -- --host 0.0.0.0
npm run lint
npm run build
```

Use `dev` for local UI work, `lint` before commits, and `build` to verify TypeScript plus production bundling.

## Coding Style & Naming Conventions

Backend code uses Python 3.11, Pydantic models, and FastAPI routers. Keep route models close to route files unless shared broadly. Prefer explicit API aliases, for example `agent_id = Field(alias="agentId")`.

Frontend code uses TypeScript, React, and Zustand. Keep types in `frontend/src/types/`, API hooks in `frontend/src/hooks/`, and page UI in `frontend/src/pages/`. Use theme variables from `frontend/src/themes/global.css`.

## Testing Guidelines

There is not yet a full automated test suite. For backend changes, run Python compile checks and add focused tests under `backend/tests/` when touching persistence, routing, or runtime behavior. For frontend changes, run `npm run lint` and `npm run build`.

For runtime features, include manual smoke cases for Chat SSE, Team orchestration, MCP, Skills, Memory, Trace, or Sandbox behavior.

## Commit & Pull Request Guidelines

Recent commits use short conventional prefixes such as:

- `feat: capture tool execution artifacts in traces`
- `fix: persist and display chat tool calls`
- `docs: add subagent implementation plan`

Prefer `feat:`, `fix:`, `docs:`, `refactor:`, or `test:`. Commit complete feature slices rather than every small edit.

Pull requests should include a summary, changed areas, verification commands, screenshots for UI changes, and notes for database or configuration changes.

## Security & Configuration Tips

Do not commit real API keys, MCP tokens, or local secrets. Configure model providers through the system settings UI or environment variables. Be careful with `backend/data/`; it may contain local chat history, memory, and registered tools.
