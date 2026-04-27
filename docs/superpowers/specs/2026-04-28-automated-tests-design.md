# Automated Tests: PyTest + Playwright

**Date:** 2026-04-28
**Approach:** Extend existing test suites (Approach B)

## Scope

1. **Playwright E2E** — Paperclip UI (Agents, Issues, Dashboard/Settings, Roles/Skills)
2. **PyTest** — hermes-gateway orchestrator (provisioning, skill sync, hot-reload, API keys, config generation)

## 1. Playwright E2E — Paperclip UI

### Location

`paperclip/tests/docker-e2e/` — new test suite alongside existing `e2e/` and `release-smoke/`.

### Config

`paperclip/tests/docker-e2e/playwright.config.ts`:
- No `webServer` — connects to running Docker Compose
- `BASE_URL` from env `PAPERCLIP_DOCKER_E2E_BASE_URL` (default `http://127.0.0.1:3100`)
- Auth credentials from env vars: `PAPERCLIP_DOCKER_E2E_EMAIL`, `PAPERCLIP_DOCKER_E2E_PASSWORD`
- Chromium only, headless, 90s timeout, 15s expect timeout
- `retries: 1` in CI, `0` locally
- Screenshots on failure, trace on failure

### Shared fixtures

`paperclip/tests/docker-e2e/fixtures.ts`:
- `signIn(page)` — navigate to `/auth`, fill email/password, submit, wait for redirect
- `apiRequest(page, method, path)` — authenticated API call via `page.request` with auth cookies
- `createTestAgent(page, companyId, name)` — helper to create agent via API
- `createTestIssue(page, companyId, opts)` — helper to create issue via API
- `cleanupTest(page, type, id)` — delete test entity after test

### Test files

#### `agents.spec.ts`

| Test case | Steps |
|-----------|-------|
| Agents list renders | Login → navigate to `/agents` → verify agent cards visible |
| Create agent | Navigate to agents → click "Create" → fill form → submit → verify in list via API |
| Agent detail page | Navigate to agent detail → verify instructions, skills tabs visible |
| Edit agent instructions | Open instructions tab → edit text → save → verify via API |
| Delete agent | Open agent detail → delete → verify removed from list |

#### `issues.spec.ts`

| Test case | Steps |
|-----------|-------|
| Issues list renders | Login → navigate to `/issues` → verify issue list/table visible |
| Create issue | Navigate to issues → click "Create" → fill title/description → submit → verify via API |
| Issue detail with checklist | Open issue detail → verify checklist rendering (done/total count) |
| Add comment | Open issue detail → type comment → submit → verify comment appears |
| Update issue status | Open issue detail → change status → verify status updated |

#### `dashboard.spec.ts`

| Test case | Steps |
|-----------|-------|
| Dashboard renders | Login → verify dashboard cards, charts, activity visible |
| Navigation | Click sidebar links → verify correct page loads |

#### `settings.spec.ts`

| Test case | Steps |
|-----------|-------|
| Instance settings page | Navigate to `/settings` → verify timezone, timeFormat controls visible |
| Change timezone | Select timezone → save → verify saved value via API |
| Toggle 24h format | Toggle timeFormat → save → verify saved value via API |

#### `roles-skills.spec.ts`

| Test case | Steps |
|-----------|-------|
| Skills list | Navigate to company skills → verify skill list visible |
| Toggle skill | Click enable/disable on a skill → verify state change via API |
| Roles page | Navigate to roles → verify role list visible |
| Create role | Click "Create Role" → fill form → submit → verify via API |

### Pattern

Each test follows the same pattern as existing `onboarding.spec.ts` and `docker-auth-onboarding.spec.ts`:
1. Login via `signIn(page)` helper
2. Navigate to target page
3. Interact with UI elements using `page.locator()`, `page.getByRole()`
4. Validate via `page.request.get()` API calls
5. Use `expect.poll()` for async operations

### Dependencies

`@playwright/test` is already in `paperclip/package.json` devDependencies. No new dependencies needed.

## 2. PyTest — hermes-gateway orchestrator

### Location

`hermes-gateway/tests/` — new test directory.

### Config

`hermes-gateway/pyproject.toml` (new):
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

### Dependencies

No new pip packages needed — uses `unittest.mock` (stdlib) for mocking.

### Shared fixtures

`hermes-gateway/tests/conftest.py`:
- `mock_db_connection` — mock `psycopg2.connect()` returning controlled cursor results
- `mock_env` — set/restore environment variables (`DATABASE_URL`, `BETTER_AUTH_SECRET`, etc.)
- `temp_profile_dir` — `tmp_path` fixture with hermes profile structure
- `sample_agent_row` — fixture returning a realistic agent DB row dict
- `mock_supervisorctl` — mock supervisor client responses

### Test files

#### `test_orchestrator.py`

| Test case | Mocks |
|-----------|-------|
| `test_fetch_agents_excludes_terminated` | psycopg2 — returns mix of active/terminated agents, assert only active returned |
| `test_fetch_agents_excludes_paused` | psycopg2 — returns mix of active/paused, assert only active |
| `test_fetch_agents_filters_hermes_local` | psycopg2 — returns agents with different adapter_types, assert only hermes_local |
| `test_ensure_hermes_installed_copies_and_patches` | shutil, subprocess — verify copytree + pip install + patch calls |
| `test_patch_installed_agent_skips_unchanged` | file hash comparison — same MD5 = no copy |
| `test_patch_installed_agent_copies_changed` | file hash comparison — different MD5 = copy |
| `test_read_paperclip_instructions_agents_md` | Path — AGENTS.md exists → returns its content |
| `test_read_paperclip_instructions_fallback` | Path — no AGENTS.md → falls back to instructions.md |
| `test_read_paperclip_instructions_soul_md` | Path — only SOUL.md exists → returns SOUL.md content |
| `test_hot_reload_detects_config_change` | hash fingerprint — different hash triggers reload |
| `test_hot_reload_skips_unchanged` | hash fingerprint — same hash = no reload |

#### `test_config_generator.py`

| Test case | Mocks |
|-----------|-------|
| `test_generate_config_basic_fields` | agent data — verify output YAML has correct agent name, model, port |
| `test_generate_config_includes_skills` | agent with skills — verify skills paths in config |
| `test_generate_config_includes_messaging` | agent with telegram config — verify telegram section |
| `test_generate_config_compression_threshold` | verify threshold value from template |
| `test_generate_config_reasoning_effort` | verify reasoning_effort from template |

#### `test_skill_importer.py`

| Test case | Mocks |
|-----------|-------|
| `test_import_custom_skill_priority` | file system — custom skill at `/opt/skills` beats builtin |
| `test_import_builtin_skill` | file system — builtin skill from hermes-agent |
| `test_import_optional_skill` | file system — optional skill |
| `test_duplicate_slug_custom_wins` | same slug in custom + builtin → custom wins |
| `test_import_creates_db_entries` | psycopg2 — verify INSERT ON CONFLICT UPDATE called |
| `test_sync_agent_skills_creates_symlinks` | file system — hermes skill → symlink |
| `test_sync_agent_skills_writes_db_files` | file system — paperclip skill → file write |
| `test_sync_agent_skills_removes_stale` | file system — old symlinks removed |

#### `test_api_keys.py`

| Test case | Mocks |
|-----------|-------|
| `test_load_keys_valid_json` | file read — valid JSON with pcp_ keys → returns dict |
| `test_load_keys_missing_file` | file read — no file → returns empty dict |
| `test_load_keys_invalid_json` | file read — malformed JSON → returns empty dict, logs error |
| `test_load_keys_validates_prefix` | JSON with non-pcp_ values → filters or warns |
| `test_key_injected_in_supervisor_config` | supervisor config — verify PAPERCLIP_RUN_API_KEY set |

### Pattern

Follow `rag-worker/tests/test_outline.py`:
- `unittest.mock.patch` and `MagicMock` for all external dependencies
- Class-based or function-based tests (match existing style)
- Plain `assert` statements
- `pytest.raises()` for error cases
- `tmp_path` for file system operations

### Run command

```bash
cd hermes-gateway && python -m pytest tests/ -v
```

## Integration

### CI (future)

Both test suites can run in CI:
- Playwright: `cd paperclip && pnpm exec playwright test --config tests/docker-e2e/playwright.config.ts`
- PyTest: `cd hermes-gateway && python -m pytest tests/ -v`

Requires Docker Compose running for Playwright tests.

### Local development

- Playwright: ensure `docker compose up -d` is running, then run specs individually
- PyTest: no external dependencies, run anytime

## File list

### New files (Playwright)
- `paperclip/tests/docker-e2e/playwright.config.ts`
- `paperclip/tests/docker-e2e/fixtures.ts`
- `paperclip/tests/docker-e2e/agents.spec.ts`
- `paperclip/tests/docker-e2e/issues.spec.ts`
- `paperclip/tests/docker-e2e/dashboard.spec.ts`
- `paperclip/tests/docker-e2e/settings.spec.ts`
- `paperclip/tests/docker-e2e/roles-skills.spec.ts`

### New files (PyTest)
- `hermes-gateway/pyproject.toml`
- `hermes-gateway/tests/__init__.py`
- `hermes-gateway/tests/conftest.py`
- `hermes-gateway/tests/test_orchestrator.py`
- `hermes-gateway/tests/test_config_generator.py`
- `hermes-gateway/tests/test_skill_importer.py`
- `hermes-gateway/tests/test_api_keys.py`
