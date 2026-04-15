# Agents

## Project Overview

HW RND AI Crew is a Docker Compose stack providing RAG over Nextcloud files, Paperclip (AI agent control plane), and Hermes agent integration. Traefik handles TLS/routing. Services run on an internal network behind `paperclip.example.com` and `rag.example.com`.

**Key services:** rag-worker (file indexer), rag-mcp (MCP search server), paperclip-server (Docker image built from `paperclip/` submodule), paperclip-db (PostgreSQL 17), Qdrant (vector DB), Ollama (local LLM).

## Conventions

- All commit messages must be written in English.
- Paperclip runs from a Docker image (`paperclip-server:latest`). After code changes in `paperclip/`, rebuild: `docker build -t paperclip-server:latest paperclip/` then `docker compose up -d paperclip-server`.

## Architecture

### Hermes Gateway (agent execution)

- Единый Docker-контейнер `hermes-gateway` с Supervisor PID 1, Python orchestrator, N gateway процессов (один на агента)
- Hermes profiles: каждый агент получает свой `~/.hermes/profiles/<agentId>/` с config.yaml, memories/, skills/, sessions/
- Orchestrator опрашивает PostgreSQL напрямую каждые 60 секунд
- Порт-маппинг: `/run/gateway-ports/ports.json` — agent_id → port (8642-8673), shared volume с paperclip-server
- Адаптер: HTTP POST к `http://hermes-gateway:<port>/v1/runs` (structured event streaming)
- `hermes-paperclip-adapter` submodule — bind-mounted в контейнер paperclip-server (ro), пересборка: `docker exec ... esbuild` в контейнере paperclip-server

### JWT Auth flow

1. Paperclip heartbeat service создаёт `heartbeat_run` в БД (реальный UUID)
2. Paperclip генерирует JWT через `createLocalAgentJwt()` с `run_id = heartbeat_run.id`
3. JWT передаётся в адаптер как `ctx.authToken`
4. Адаптер прокидывает JWT в `POST /v1/runs` как `paperclip_api_key`
5. Gateway `api_server.py` обновляет `os.environ["PAPERCLIP_RUN_API_KEY"]` и **пересоздаёт** MCP-подключение paperclip (отключает старое, при создании агента MCP подключается с новым JWT)
6. MCP tools используют JWT в заголовке `X-Paperclip-Api-Key`

### MCP JWT staleness (исправлено)

MCP-серверы в hermes-agent подключаются один раз и **кешируются глобально** (`_servers` dict в `mcp_tool.py`). Обновление `os.environ["PAPERCLIP_RUN_API_KEY"]` недостаточно — существующее соединение использует старые заголовки. Решение: в `_handle_runs` (api_server.py) перед созданием агента принудительно отключается MCP-сервер `paperclip`, чтобы при `_create_agent` → `discover_mcp_tools()` он переподключился с новым JWT.

### Adapter resultJson (исправлено)

Paperclip heartbeat service читает `adapterResult.resultJson` для:
- Записи результата в `heartbeat_runs.result_json`
- Создания комментария к задаче (`buildHeartbeatRunIssueComment`)
- Отображения в UI

Адаптер **должен** возвращать `resultJson: { summary: "..." }` — без этого run считается "succeeded" но без deliverable. Поле `summary` на верхнем уровне адаптера НЕ достаточно — Paperclip читает именно `resultJson`.

### delegate_task disable (исправлено)

`get_tool_definitions()` в `model_tools.py` — когда передан `enabled_toolsets`, блок `disabled_toolsets` полностью игнорировался (баг в оригинале). Исправлено: `disabled_toolsets` обрабатывается **после** `enabled_toolsets`, исключая инструменты из собранного набора.

## Discoveries

### Budget policies
- Политики уникальны по `(companyId, scopeType, scopeId, metric, windowKind)` — один scope может иметь две политики: `billed_cents` и `total_tokens`
- `migratePoliciesMetric()` деактивирует (`isActive=false, amount=0`) вместо DELETE (который ломал FK на `budget_incidents`)

### paperclip-server deployment
- Контейнер `paperclip-server` работает из образа `paperclip-server:latest`. Исходники в `/app/server/dist/` — скомпилированный ESM JS
- **UI dist bind-mounted**: `./paperclip/ui/dist:/app/ui/dist` (rw) — Vite build в контейнере пишет на хост
- **Server dist НЕ bind-mounted** — нужен `docker cp` + `docker compose restart` для серверных фиксов
- **Adapter bind-mounted (ro)**: `./hermes-paperclip-adapter/dist/` → отдельные файлы в `/app/node_modules/.pnpm/hermes-paperclip-adapter@0.2.0/...`
- UI: `docker exec -w /app/ui paperclip-server node node_modules/vite/bin/vite.js build`
- Shared package: `docker exec -w /app paperclip-server npx tsc -p packages/shared/tsconfig.json`
- Server файлы: esbuild в контейнере: `docker exec -w /app paperclip-server node -e "..."` с esbuild API
- **Adapter build на хосте нет node** — билдить в контейнере: `docker exec -w /tmp/adapter-build paperclip-server /app/node_modules/.bin/esbuild src/server/execute.ts --outfile=/tmp/adapter-dist/server/execute.js --format=esm --platform=node --target=node20 --bundle=false`

### Docker build cache bug
- `docker build` с кэшем может не обновлять COPY слои если контекст не изменился (хэш совпадает)
- `docker compose up -d --force-recreate` НЕ перестраивает образ — использует закэшированный
- `docker compose up -d --force-recreate --build` — правильно: билдит + пересоздаёт
- Контейнер может использовать **старый image ID** если compose кэшировал ссылку — всегда проверять `docker inspect <container> --format='{{.Image}}'` vs `docker inspect <image>:latest --format='{{.Id}}'`

### hermes-agent pip install lifecycle
- Оркестратор копирует submodule `HERMES_SRC` (`/opt/hermes-agent`) → `HERMES_BUILD` (`/opt/hermes-agent-build`) через `shutil.copytree(..., dirs_exist_ok=True)`
- Затем `pip install HERMES_BUILD` → файлы попадают в `/usr/local/lib/python3.11/site-packages/`
- **Патчи в submodule НЕ попадают** в установленный пакет если `HERMES_BUILD` уже существует (`dirs_exist_ok=True` не перезаписывает)
- Решение: `_patch_installed_agent()` в оркестраторе — копирует изменённые файлы из submodule в site-packages по MD5 хэшу

### SES lockdown (MetaMask extension)
- `Intl.supportedValuesOf("timeZone")` ломается в SES lockdown — React error #310 ("Too many re-renders")
- Решение: статический список timezone вместо Intl API

### APT/pip mirrors
- Yandex APT mirror (`mirror.yandex.ru`) работает для Debian Trixie
- Yandex pip mirror (`pypi.yandex-team.ru`) **недоступен** — fallback на PyPI

## Relevant files / directories

### Hermes Gateway:
- `hermes-gateway/Dockerfile` — Yandex APT mirror
- `hermes-gateway/orchestrator/orchestrator.py` — orchestrator + `_patch_installed_agent()` (hash-based copy)
- `docker-compose.yml` — ui/dist bind mount, hermes-gateway service, adapter bind mounts

### Hermes Agent (patched submodule):
- `hermes-agent/gateway/platforms/api_server.py` — `disabled_toolsets=["delegation"]`, MCP paperclip reconnect on JWT update
- `hermes-agent/model_tools.py` — `disabled_toolsets` applied after `enabled_toolsets` (bugfix)

### Hermes Paperclip Adapter (submodule):
- `hermes-paperclip-adapter/src/server/execute.ts` — gateway mode execute, `resultJson` return
- `hermes-paperclip-adapter/dist/server/execute.js` — bind-mounted (ro) в paperclip-server
- Build: esbuild в контейнере paperclip-server (нет node на хосте)

### Paperclip UI (modified):
- `paperclip/ui/src/pages/Costs.tsx` — dual-metric budget cards
- `paperclip/ui/src/pages/AgentDetail.tsx` — budget cards, transcript hiddenTypes/toggle
- `paperclip/ui/src/pages/InstanceGeneralSettings.tsx` — Regional block (timezone + 24h)
- `paperclip/ui/src/lib/utils.ts` — formatDateTime/formatDate с timezone opts
- `paperclip/ui/src/hooks/useTimeSettings.ts` — timezone, timeFormat hooks
- `paperclip/ui/src/components/transcript/RunTranscriptView.tsx` — timestamps, Brain icon, filters

### Paperclip Server (modified):
- `paperclip/server/src/services/budgets.ts` — `migratePoliciesMetric` деактивирует вместо DELETE
- `paperclip/server/src/services/instance-settings.ts` — timezone/timeFormat defaults

### Shared package (modified):
- `paperclip/packages/shared/src/types/instance.ts` — TimeFormat type, timezone/timeFormat fields
- `paperclip/packages/shared/src/validators/instance.ts` — timezone/timeFormat zod schemas
