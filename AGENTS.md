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
- Provizioning: только агенты из `company_memberships` (principal_type='agent') с `adapter_type='hermes_local'` и status не terminated/paused
- Порт-маппинг: `/run/gateway-ports/ports.json` — agent_id → port (8642-8673), shared volume с paperclip-server
- Адаптер: HTTP POST к `http://hermes-gateway:<port>/v1/runs` (structured event streaming)
- `hermes-paperclip-adapter` submodule — bind-mounted в контейнер paperclip-server (ro), пересборка: `docker exec ... esbuild` в контейнере paperclip-server
- Hot-reload: hash fingerprint (config-template.yaml + orchestrator.py + config_generator.py) — при изменении исходников оркестратор перезапускает агентов автоматически
- **Инструкции агентов**: источник истины — Paperclip UI (`/agents/<slug>/instructions`), managed bundle на диске paperclip-server. Оркестратор монтирует `paperclip_data` (ro) и при provisioning'е читает `<instanceRoot>/companies/<companyId>/agents/<agentId>/instructions/AGENTS.md` → пишет в `SOUL.md` профиля hermes. Fallback — минимальная заглушка из `_build_soul_md()`.

### JWT Auth flow

1. Paperclip heartbeat service создаёт `heartbeat_run` в БД (реальный UUID)
2. Paperclip генерирует JWT через `createLocalAgentJwt()` с `run_id = heartbeat_run.id`
3. JWT передаётся в адаптер как `ctx.authToken`
4. Адаптер прокидывает JWT в `POST /v1/runs` как `paperclip_api_key`
5. Gateway `api_server.py` обновляет `os.environ["PAPERCLIP_RUN_API_KEY"]` и **пересоздаёт** MCP-подключение paperclip (отключает старое, при создании агента MCP подключается с новым JWT)
6. MCP tools используют JWT в заголовке `X-Paperclip-Api-Key`

### Outline MCP (knowledge base)

- Endpoint: `https://outline.collaborationism.tech/mcp` (StreamableHTTP)
- Auth: shared API token (`ol_api_...`) в `Authorization: Bearer` заголовке
- Env var: `MCP_OUTLINE_API_KEY` в `.env`, прокидывается в `hermes-gateway` и `paperclip-server`
- Конфигурация: `hermes-gateway/config-template.yaml` и `hermes-shared-config/config.yaml`
- Инструкции агентам: в `_build_soul_md()` (`orchestrator.py`)
- Агенты используют `mcp_outline_*` tools для поиска и создания/обновления документов
- Перед созданием документа — всегда поиск (`mcp_outline_search`), чтобы избежать дубликатов
- `documents.create` возвращает ProseMirror + Markdown. Для чтения созданного документа всегда используй `documents.info` — он возвращает чистый Markdown

### Outline RAG (search)

- rag-worker индексирует документы Outline → Qdrant коллекция `outline_docs` (markdown chunks)
- rag-mcp предоставляет tool `search_outline` для семантического поиска
- `list_outline_documents` — просмотр проиндексированных документов
- Агенты используют `search_outline` для чтения/поиска документов Outline (вместо `mcp_outline_*`)
- `mcp_outline_*` используется только для создания и обновления документов
- Env vars: `OUTLINE_URL`, `OUTLINE_API_KEY`, `OUTLINE_SYNC_INTERVAL` (default 300s), `OUTLINE_QDRANT_COLLECTION` (default `outline_docs`)
- Outline API возвращает Markdown через поле `text` в `/api/documents.info` (не нужен `?format=markdown`)
- Sync запускается через FastAPI `lifespan` (daemon thread) — логи daemon thread не видны в `docker logs`, но sync работает (проверка: `docker exec rag-worker python -c "from rag.main import sync_outline; print(sync_outline())"`)
- `/status/outline` endpoint — кол-во документов и чанков
- Env vars `OUTLINE_*` дублируются в `docker-compose.yml` `environment` (не только `env_file`) — нужно для корректного проброса при пустых значениях в `.env`
- rag-worker и rag-mcp — git submodules. Коммиты внутри submodule не видны в основном репо пока не обновить submodule reference

### Per-Agent Messaging (Telegram)

- Messaging конфиг хранится в `agents.adapter_config.messaging.telegram` (per-agent, jsonb)
- Оркестратор читает `adapter_config` из БД и подставляет telegram конфиг в config.yaml агента
- Каждый агент может иметь свой Telegram bot token
- UI: вкладка "Messaging" на странице агента (AgentDetail)
- Instance-level messaging (`instance_settings.messaging`) больше не используется
- **Group trigger**: `require_mention=true` + `mention_patterns` из имени агента (regexp `\b<AgentName>\b`). Агент отвечает в группе только если: reply на его сообщение, @mention, или имя в тексте
- `TELEGRAM_ALLOWED_USERS` пробрасывается из `adapter_config.messaging.telegram.allowedUsers` — пользователи авторизуются автоматически без pairing code

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

### Stale JWT run_id FK violation (исправлено)

Hermes gateway может держать старый JWT после того как соответствующий `heartbeat_run` удалён (reaped orphaned runs, server restart, etc). Все таблицы с FK на `heartbeat_runs.id` (`issue_comments.created_by_run_id`, `document_revisions.created_by_run_id`, `activity_log.run_id`) ломались с 500 при INSERT.

Решение: валидация в `actorMiddleware` (`auth.ts`) — если `req.actor.runId` из JWT ссылается на несуществующий run, middleware очищает его в `undefined` и логирует warn. Один DB-запрос на запрос, покрывает все downstream FK.

### Agent Memory Service

Векторизованная память агентов — session history и MEMORY.md → Qdrant, доступ через MCP tools.

- **session_indexer.py** — Supervisor процесс в hermes-gateway. Каждые 10 мин сканирует `profiles/*/sessions/*.jsonl` и `memories/MEMORY.md`, извлекает assistant-сообщения, эмбеддит через Ollama (nomic-embed-text, 768d), upsert в Qdrant collection `agent_memory`
- **memory_mcp_server.py** — MCP StreamableHTTP server на порту 8680. Tools: `search_memory(query)`, `get_agent_context(agent_name)`
- Индексер отслеживает файлы по mtime+size хэшу (state: `profiles/indexer-state.json`). При ошибке embed файл НЕ помечается обработанным — retry на следующем цикле
- BATCH_SIZE=1, MAX_TEXT_LEN=1000 — Ollama nomic-embed-text нестабилен на больших батчах/текстах
- Коллекция Qdrant `agent_memory`: 768d cosine, payload indexes на `agent_name` (keyword), `source` (keyword)
- Профили агентов персистятся через Docker volume `hermes_profiles` → `/root/.hermes/profiles`
- Конфигурация: `memory` mcp_server в `config-template.yaml` / `config.yaml`, переменные `OLLAMA_BASE_URL`, `QDRANT_URL`, `EMBED_MODEL`, `MEMORY_API_KEY`

## Discoveries

### Budget policies
- Политики уникальны по `(companyId, scopeType, scopeId, metric, windowKind)` — один scope может иметь две политики: `billed_cents` и `total_tokens`
- `migratePoliciesMetric()` деактивирует (`isActive=false, amount=0`) вместо DELETE (который ломал FK на `budget_incidents`)

### paperclip-server deployment
- Контейнер `paperclip-server` работает из образа `paperclip-server:latest`. Исходники в `/app/server/dist/` — скомпилированный ESM JS
- **UI dist bind-mounted**: `./paperclip/ui/dist:/app/ui/dist` (rw) — Vite build в контейнере пишет на хост
- **UI src НЕ bind-mounted** — перед `vite build` нужно `docker cp paperclip/ui/src/... paperclip-server:/app/ui/src/...` для каждого изменённого файла
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

### formatDateTime без настроек (исправлено)
- Многие компоненты вызывают bare `formatDateTime()` из `lib/utils.ts` без `{ timezone, timeFormat }` — всегда 12h по умолчанию
- Исправлено: `CommentThread.tsx`, `FinanceTimelineCard`, `LiveRunWidget`, `ExecutionWorkspaceDetail`, `InstanceSettings`, `ExecutionWorkspaceCloseDialog` — все используют `useTimeSettings()` hook

### FastAPI on_event deprecation
- FastAPI >= 0.100 deprecated `@app.on_event("startup")` — в 0.136+ не вызывается
- Решение: `lifespan` context manager (`from contextlib import asynccontextmanager`)
- rag-worker использует lifespan для запуска Outline sync background thread

### Outline API
- `/api/documents.list` — пагинация через `offset`/`limit`, `pagination.total` для определения конца
- `/api/documents.info` — поле `text` содержит Markdown. Внутреннее хранение — ProseMirror JSON (`data.content`), API конвертирует Markdown↔ProseMirror при записи/чтении. Ответ содержит оба формата, но `text` — всегда Markdown
- Запись (create/update): принимает Markdown через параметр `text`
- `updatedAt` — ISO 8601 формат (`2026-04-19T10:00:00.000Z`), парсинг через `datetime.fromisoformat`
- `isDeleted: true` — мягкое удаление, нужно фильтровать при list
- Auth: `Authorization: Bearer ol_api_...` заголовок

## Relevant files / directories

### Hermes Gateway:
- `hermes-gateway/Dockerfile` — Yandex APT mirror
- `hermes-gateway/orchestrator/orchestrator.py` — orchestrator + `_patch_installed_agent()` (hash-based copy)
- `hermes-gateway/orchestrator/session_indexer.py` — cron indexer for agent memory (Ollama embed → Qdrant)
- `hermes-gateway/orchestrator/memory_mcp_server.py` — MCP server for `search_memory` / `get_agent_context`
- `hermes-gateway/supervisord.conf` — session-indexer + memory-mcp programs
- `docker-compose.yml` — ui/dist bind mount, hermes-gateway service, adapter bind mounts, hermes_profiles volume

### RAG Worker (Outline RAG):
- `rag-worker/rag/outline.py` — Outline REST API client (`list_documents`, `get_document_markdown`, `list_collections`)
- `rag-worker/rag/main.py` — `sync_outline()` (incremental sync), background thread (lifespan), `/status/outline` endpoint
- `rag-worker/rag/qdrant_client.py` — outline collection helpers (`ensure_outline_collection`, `upsert_outline_chunks`, etc.)
- `rag-worker/tests/test_outline.py` — Outline client unit tests (mock httpx)

### RAG MCP (Outline search):
- `rag-mcp/mcp_server/tools.py` — `search_outline()`, `list_outline_documents()` + existing Nextcloud tools
- `rag-mcp/mcp_server/main.py` — MCP tool registration, StreamableHTTP transport

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
- `paperclip/ui/src/components/CommentThread.tsx` — использует `useTimeSettings()` для 24h/12h
- `paperclip/ui/src/components/transcript/RunTranscriptView.tsx` — timestamps, Brain icon, filters

### Paperclip Server (modified):
- `paperclip/server/src/services/budgets.ts` — `migratePoliciesMetric` деактивирует вместо DELETE
- `paperclip/server/src/services/instance-settings.ts` — timezone/timeFormat defaults

### Shared package (modified):
- `paperclip/packages/shared/src/types/instance.ts` — TimeFormat type, timezone/timeFormat fields
- `paperclip/packages/shared/src/validators/instance.ts` — timezone/timeFormat zod schemas

## Discoveries

### Platform bugs (confirmed, not fixable from our side)

| # | Bug | Workaround |
|---|-----|------------|
| 1 | `list_issues(assigneeAgentId="me")` → HTTP 500 | Передавать свой UUID явно (из `paperclip_get_current_agent`) или фильтровать по статусу |
| 2 | `release_issue()` сбрасывает статус в «todo» и снимает исполнителя | Использовать `update_issue` вместо `release_issue`, если нужно сохранить статус и assignee |
| 3 | `read_file` «File unchanged since last read» при повторном чтении cache-файлов | Использовать `terminal cat` вместо `read_file` |

### Roles system
- `assignedRole` must be in `createAgentSchema` (Zod validator) or `validate()` strips it from `req.body` silently
- `resolveRoleKey()` must check UUID format before querying UUID column — otherwise PostgresError on string keys like `agency-agents/marketing/foo`
- `role_sources` DELETE needs cascade: first delete `company_roles` with matching `sourceId`, then delete source
- `materializeDefaultInstructionsBundleForNewAgent`: when `promptTemplate` is non-empty (from role), it only created `AGENTS.md`. Fixed to merge default bundle files (HEARTBEAT.md, SOUL.md) with role's AGENTS.md
- Default agent bundle: `["AGENTS.md", "HEARTBEAT.md", "SOUL.md"]` — same structure as CEO minus TOOLS.md
- Onboarding assets resolved from `dist/onboarding-assets/` (not `src/`) — new files must be copied to both locations in container

### ServiceWorker cache
- `sw.js` uses `CACHE_NAME` version string — must bump on every UI deploy or browser serves stale assets
- Firefox caches aggressively — even Ctrl+Shift+R insufficient. Must bump `CACHE_NAME` and deploy updated `sw.js`

### Context compression
- Hermes config `compression.threshold` controls when context auto-compresses (fraction of model context length)
- Changed from 0.6 (60%) to 0.85 (85%) — agents use more context before compression kicks in
- Config hot-reload via hash fingerprint in orchestrator — change `config-template.yaml` + bump `_config_version`

### Hermes adapter config
- `buildSchemaAdapterConfig()` does NOT include `promptTemplate` — it's adapter-agnostic and handled server-side
- Backend fills `promptTemplate` from role markdown when `assignedRole` is provided and `promptTemplate` is empty

### release_issue() fixed (was resetting status/assignee)
- `release()` in `paperclip/server/src/services/issues.ts` now only clears `checkoutRunId` — preserves `status` and `assigneeAgentId`
- "Release" means "release the write lock", not "abandon the issue"
- To change status or reassign, agents should use `update_issue` explicitly

### list_issues assigneeAgentId=me fixed
- Server route now resolves `assigneeAgentId=me` to `req.actor.agentId` for agent actors (like userId filters)
- MCP tool returns explicit error if agent ID is not available after "me" resolution

### rag-mcp response serialization fixed
- `rag-mcp/mcp_server/main.py` now uses `json.dumps(result, ensure_ascii=False, default=str)` instead of `str(result)`
- Was producing Python repr (single quotes, None, True/False) inside JSON wrapper — broke agent-side parsing

### Outline NDJSON response handling
- `rag-worker/rag/outline.py` — `_parse_json_response()` handles both regular JSON and NDJSON (objects separated by newline)
- Falls back to line-by-line parsing when `resp.json()` fails

### Paperclip 409 conflict handling
- `paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py` — `_request()` returns structured 409 error with `hint` field
- Hint tells agents to save work to Outline/disk and ask CEO to update manually
