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

### Agent Auth flow (permanent API keys)

1. Paperclip heartbeat service создаёт `heartbeat_run` в БД (реальный UUID)
2. Оркестратор загружает постоянные `pcp_*` API ключи из `agent_api_keys.json` и прописывает в supervisor config как `PAPERCLIP_RUN_API_KEY`
3. Адаптер получает `ctx.runId` (heartbeat run UUID) и `ctx.authToken` (JWT) от Paperclip
4. Адаптер отправляет `POST /v1/runs` с `heartbeat_run_id: ctx.runId` и `paperclip_api_key: ctx.authToken`
5. Gateway `api_server.py` проверяет: если `PAPERCLIP_RUN_API_KEY` уже `pcp_*` — не перезаписывает. Устанавливает `PAPERCLIP_HEARTBEAT_RUN_ID` из `heartbeat_run_id` body
6. MCP paperclip переподключается: `${PAPERCLIP_RUN_API_KEY}` → permanent key, `${PAPERCLIP_HEARTBEAT_RUN_ID}` → heartbeat UUID
7. paperclip-mcp получает `X-Paperclip-Api-Key: pcp_*` + `X-Paperclip-Run-ID: <uuid>` и прокидывает в paperclip-server
8. paperclip-server авторизует через `pcp_*` key (идентифицирует агента), опциональный `X-Paperclip-Run-ID` для FK linking

**Преимущество:** постоянные ключи НЕ истекают, нет 401 "Agent run id required" при удалённых heartbeat_runs

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

### Issue Checklist

Нативный чеклист задач — замена PROGRESS.md, персистентный в БД Paperclip.

- **DB**: `checklist` jsonb column на `issues` table (migration 0052), тип `IssueChecklistItem[]` = `{ text: string, done: boolean }`
- **MCP tool**: `paperclip_set_checklist` в paperclip-mcp — полная замена чеклиста (agent отправляет весь массив)
- **UI**: read-only рендер в `IssueProperties.tsx` — CheckSquare/Square иконки, прогресс done/total, line-through для done
- **Валидация**: max 20 items, text max 200 chars (Zod schema в shared)
- Панель "Properties" переименована в "Details"
- Агенты используют чеклист вместо PROGRESS.md — инструкции обновлены в AGENTS.md (Paperclip instructions volume) и `prompt-template.md`
- **paperclip-mcp deployment**: контейнер не bind-mounted — нужен `docker cp` файлов + `docker restart paperclip-mcp` для деплоя изменений
- **MCP tool naming**: hermes-agent добавляет двойной префикс `mcp_paperclip_` → tools называются `mcp_paperclip_paperclip_list_issues`. Инструкции агентам должны использовать полный префикс `mcp_paperclip_`

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

### Paperclip MCP (submodule):
- `paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py` — MCP tool implementations (`set_checklist`, `list_issues`, etc.)
- `paperclip-mcp/paperclip-mcp-backup/mcp_server/main.py` — MCP tool registration, dispatch, StreamableHTTP transport
- Deployment: `docker cp` files → `docker restart paperclip-mcp` (container not bind-mounted)
- 23 tools registered including `paperclip_set_checklist`

### Paperclip UI (modified):
- `paperclip/ui/src/pages/Costs.tsx` — dual-metric budget cards
- `paperclip/ui/src/pages/AgentDetail.tsx` — budget cards, transcript hiddenTypes/toggle
- `paperclip/ui/src/pages/InstanceGeneralSettings.tsx` — Regional block (timezone + 24h)
- `paperclip/ui/src/lib/utils.ts` — formatDateTime/formatDate с timezone opts
- `paperclip/ui/src/hooks/useTimeSettings.ts` — timezone, timeFormat hooks
- `paperclip/ui/src/components/CommentThread.tsx` — использует `useTimeSettings()` для 24h/12h
- `paperclip/ui/src/components/transcript/RunTranscriptView.tsx` — timestamps, Brain icon, filters
- `paperclip/ui/src/components/IssueProperties.tsx` — checklist rendering (CheckSquare/Square, progress)
- `paperclip/ui/src/components/PropertiesPanel.tsx` — "Details" panel title

### Paperclip Server (modified):
- `paperclip/server/src/services/budgets.ts` — `migratePoliciesMetric` деактивирует вместо DELETE
- `paperclip/server/src/services/instance-settings.ts` — timezone/timeFormat defaults

### Shared package (modified):
- `paperclip/packages/shared/src/types/instance.ts` — TimeFormat type, timezone/timeFormat fields
- `paperclip/packages/shared/src/validators/instance.ts` — timezone/timeFormat zod schemas
- `paperclip/packages/shared/src/types/issue.ts` — IssueChecklistItem type, checklist field
- `paperclip/packages/shared/src/validators/issue.ts` — issueChecklistItemSchema, issueChecklistSchema

### DB (modified):
- `paperclip/packages/db/src/schema/issues.ts` — checklist jsonb column
- `paperclip/packages/db/src/migrations/0052_issue_checklist.sql` — ALTER TABLE migration

## Discoveries

### Platform bugs (confirmed, not fixable from our side)

| # | Bug | Workaround |
|---|-----|------------|
| 1 | `list_issues(assigneeAgentId="me")` → HTTP 500 | **FIXED** — server route now resolves `me` to agent UUID |
| 2 | `release_issue()` сбрасывает статус в «todo» и снимает исполнителя | **FIXED** — `release()` now only clears `checkoutRunId` |
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
- Ядерный вариант: добавить `Clear-Site-Data: "cache"` заголовок к `index.html` через Express middleware ПЕРЕД `express.static()` — заставляет браузер очистить весь кеш
- Middleware патчится в `/app/server/dist/app.js` (в контейнере) — не переживает `docker compose up -d --build`
- После подтверждения что кеш сброшен — убрать заголовок (он отключает оффлайн-кеш полностью)

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

### MCP tool naming (IMPORTANT)
- Hermes-agent добавляет двойной префикс `mcp_<server>_` к tool names из MCP servers
- Paperclip MCP tools: `paperclip_list_issues` → `mcp_paperclip_paperclip_list_issues` в агенте
- Агенты (glm-5.1) НЕ понимают маппинг `paperclip_*` → `mcp_paperclip_paperclip_*` — инструкции должны использовать полные имена `mcp_paperclip_paperclip_*`
- Инструкции в SOUL.md и prompt-template.md должны явно указывать префикс `mcp_paperclip_`

### paperclip-mcp deployment
- Контейнер `paperclip-mcp` НЕ bind-mounted — submodule файлы нужно копировать явно
- Deploy: `docker cp paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py paperclip-mcp:/app/mcp_server/tools.py` + same for `main.py` + `docker restart paperclip-mcp`
- MCP StreamableHTTP требует `Accept: application/json, text/event-stream` заголовок — без него 406
- MCP protocol требует initialize handshake перед `tools/list` — иначе `WARNING:root:Failed to validate request`

### Outline NDJSON response handling
- `rag-worker/rag/outline.py` — `_parse_json_response()` handles both regular JSON and NDJSON (objects separated by newline)
- Falls back to line-by-line parsing when `resp.json()` fails

### Paperclip 409 conflict handling
- `paperclip-mcp/paperclip-mcp-backup/mcp_server/tools.py` — `_request()` returns structured 409 error with `hint` field
- Hint tells agents to save work to Outline/disk and ask CEO to update manually

### checkout_run_id stale lock (исправлено)

**Симптом:** При последовательных heartbeat runs агент получает 409 на `checkout_issue` — предыдущий run оставил `checkout_run_id` на issue, но run уже завершён (succeeded). `executionRunId` очищается сервером, а `checkoutRunId` — нет.

**Root cause:** `releaseIssueExecutionAndPromote` в heartbeat.js очищала `executionRunId`/`executionAgentNameKey`/`executionLockedAt` при финализации run'а, но НЕ очищала `checkoutRunId`. Следующий run того же агента пытался `checkout_issue` → 409 (checkoutRunId указывает на старый run).

**Fix:** Добавлена очистка `checkoutRunId: null` в `releaseIssueExecutionAndPromote` (2 места в heartbeat.js). Патч применяется в entrypoint (`paperclip-entrypoint.sh`) через sed при каждом старте контейнера — переживает `docker compose up -d --build`.

**Файл:** `/app/server/dist/services/heartbeat.js` (в контейнере paperclip-server)

### Agent prompt loading priority (IMPORTANT)
- Adapter `execute.ts` has `DEFAULT_PROMPT_TEMPLATE` hardcoded, but `loadPromptTemplate()` checks `/paperclip/prompt-template.md` FIRST
- **`/paperclip/prompt-template.md` overrides the JS default** — always edit the file on disk, not just the JS source
- After editing `execute.ts` source → rebuild adapter (`esbuild` in container) → restart paperclip-server
- After editing `/paperclip/prompt-template.md` → just restart paperclip-server (no rebuild needed)

### Text-only responses and run termination (glm-5.1) — FIXED

**Симптом:** glm-5.1 отвечает текстом без tool_calls. Run "succeeds" с `resultJson` содержащим обещание ("Загружу в Outline", "Создам документ") вместо результата.

**Root cause analysis:**

1. **Начало run — МИНИМАЛЬНЫЙ user message** (FIXED). Адаптер отправлял `input: "Work on the assigned task"` (25 chars). Рабочий hermes-agent использует детальные cron prompt'ы (1.9K+ chars) как user message. Модели приоритизируют user message над system prompt. Fix: `buildInputMessage()` в adapter — формирует task-specific user message ~400 chars с `[HEARTBEAT RUN]` префиксом.

2. **Конец run — text-only termination без retry** (FIXED). Когда модель отвечает текстом без tool_calls, `run_agent.py` делает `break` без проверки. Fix: promise detection (`_has_russian_promise`/`_has_english_promise`) — если ответ похож на обещание, inject continuation prompt и `continue` loop (до 2 раз).

**Сравнение с рабочим hermes-agent (`/mnt/services/hermes-agent/`):**

| Aspect | Working | Ours (before fix) | Ours (after fix) |
|--------|---------|-------------------|-------------------|
| System prompt | "You are Hermes Agent..." (14.9K) | SOUL.md persona (7.5K) | Same |
| Tools | **222** (browser, delegation, etc.) | **69** | Same |
| User message | Cron prompt (1.9K+ chars) | `"Work on the assigned task"` (25 chars) | `[HEARTBEAT RUN]...` (~400 chars) |
| `tool_use_enforcement` | `auto` | `true` | Same |
| Text-only retry | N/A (model doesn't text-only) | None | Promise detection + continuation |
| `compression.threshold` | 0.6 | 0.85 | Same |

**Патчи в `hermes-agent/run_agent.py`:**
- `_text_only_continuations` counter (init at line ~7041)
- Promise detection functions (`_has_russian_promise`, `_has_english_promise`)
- Forced continuation loop (up to 2 retries) before `break`

**Патчи в `hermes-paperclip-adapter/src/server/execute.ts`:**
- `buildInputMessage()` — task-specific user message
- Используется как `input` в POST /v1/runs

**Дампы API запросов (HERMES_DUMP_REQUESTS=1):**
- Env var добавлен в supervisor config для каждого gateway процесса
- Дампы сохраняются в `<profile>/sessions/request_dump_<session_id>_<timestamp>.json`
- Формат: `{timestamp, session_id, reason, request: {method, url, headers, body}}`
- Reason: `preflight` (перед каждым API вызовом), `non_retryable_client_error`, `max_retries_exhausted`
- 41 последовательный text-only run с 08:26 до 13:52 (все `msgs=2`, `user="Work on the assigned task"`)
- После fix: 24 API calls за один run, agent выполнял реальную работу

**Критичный баг с патчами:** `_patch_installed_agent()` в orchestrator копирует из `hermes-agent/` submodule → site-packages. Патчи site-packages переживают supervisor restart, но **НЕ** переживают `docker compose up -d --build` (image rebuild). Патчи нужно сохранять в submodule (`hermes-agent/run_agent.py`, `hermes-agent/gateway/platforms/api_server.py`). Также: `gateway.platforms.api_server` в site-packages — **отдельный файл** от `/opt/hermes-agent-build/gateway/platforms/api_server.py`; нужно копировать явно: `docker exec hermes-gateway cp /opt/hermes-agent-build/gateway/platforms/api_server.py /usr/local/lib/python3.11/site-packages/gateway/platforms/api_server.py`

**Код path для Paperclip heartbeat:** adapter → `POST /v1/runs` → `api_server.py` → `AIAgent.run_conversation()` (в site-packages `run_agent.py`). Telegram gateway использует `gateway/run.py` → `GatewayRunner` (другой код path, кеширование AIAgent, etc).

**Контекст между runs:** `paperclip_set_checklist` (чеклист задачи, персистентный в БД) + файлы на диске. PROGRESS.md больше не используется — заменён на нативный чеклист.

### Agent instruction files (container volume)
- Путь: `/paperclip/instances/default/companies/<companyId>/agents/<agentId>/instructions/`
- Файлы: `AGENTS.md` (role-specific), `SOUL.md` (persona), `HEARTBEAT.md` (optional, merged into adapter prompt)
- Оркестратор читает эти файлы и синкает в hermes profile при provisioning
- Изменения в UI `/agents/<slug>/instructions` → пишутся в этот volume → подхватываются при следующем sync

### Config: reasoning_effort
- `agent.reasoning_effort: "none"` в `config-template.yaml` — загружается через `_load_reasoning_config()` в `gateway/run.py`
- api_server.py патчен для передачи `reasoning_config` в AIAgent: `from gateway.run import GatewayRunner as _GR; _reasoning_config = _GR._load_reasoning_config()`
- Патч api_server.py нужно копировать явно: `cp /opt/hermes-agent-build/gateway/platforms/api_server.py /usr/local/lib/python3.11/site-packages/gateway/platforms/api_server.py`

### Session indexer bug
- `session_indexer.py` каждые 10 мин: `ERROR: Index cycle failed: cannot access local variable 'failed_sources' where it is not associated with a value`
- Индексер продолжает работать (ошибка в logging/telemetry, не в индексации), но логи засираются

### MCP memory server connection issue
- `Failed to connect to MCP server 'memory': Illegal header value b'[REDACTED]'`
- Memory MCP server на порту 8680 запускается корректно, но gateway не может подключиться
- Возможная причина: невалидный символ в `MEMORY_API_KEY` или problem с StreamableHTTP transport

### Supervisor config reload (CRITICAL)

- **`supervisorctl restart` НЕ перечитывает config** — только убивает/запускает процесс со старым конфигом
- Для применения нового config: `supervisorctl reread && supervisorctl update <process_name>`
- Или: `docker exec hermes-gateway supervisorctl reread && docker exec hermes-gateway supervisorctl update`
- После изменений в orchestrator (`agent_api_keys.json`, `config_generator.py`) — ALWAYS reread+update, не просто restart
- Проверить env var процесса: `cat /proc/<PID>/environ | tr '\0' '\n' | grep PAPERCLIP`

### Paperclip MCP tools disappearing (исправлено)

**Симптом:** Агент теряет paperclip MCP tools (44t/0pc вместо 71t/27pc) после первого heartbeat run. Outline/rag/memory tools стабильны.

**Root cause (двойной):**
1. `supervisorctl restart` не перенидывал config → процесс стартовал с JWT вместо `pcp_*` permanent key → JWT протухал между runs → MCP reconnect с протухшим JWT → paperclip-mcp отклонял → tools=0
2. `MCPServerTask._run_http()` держит StreamableHTTP connection. При idle (>5 мин) httpx timeout рвёт соединение. `run()` пытается reconnect, но после 5 неудачных попыток сдаётся. `_servers["paperclip"]` остаётся с `session=None`, а `discover_mcp_tools()` skip'ает (т.к. paperclip уже в `_servers`)

**Фикс:**
- `supervisorctl reread && supervisorctl update` для применения permanent keys
- `api_server.py`: evict paperclip из `_servers` если `session is None` — позволяет `discover_mcp_tools()` переподключить
- `_has_permanent_key` guard: если env var уже `pcp_*` — не перезаписывать JWT от adapter'а

### JWT staleness → 401 "Agent run id required" (исправлено)

**Решение:** Постоянные `pcp_*` API ключи вместо per-run JWT. Ключи хранятся в `agent_api_keys.json` и прописываются в supervisor config как `PAPERCLIP_RUN_API_KEY`. Gateway `api_server.py` не перезаписывает их JWT. `X-Paperclip-Run-ID` header передаётся отдельно через `${PAPERCLIP_HEARTBEAT_RUN_ID}` env var для опционального FK linking.

**Оставшийся edge case:** `X-Paperclip-Run-ID` может ссылаться на удалённый heartbeat_run. `actorMiddleware` в auth.ts очищает `runId` в `undefined` — запрос выполняется без FK linking (без ошибки 401).
