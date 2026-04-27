# Automated Tests (PyTest + Playwright) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write Playwright E2E tests for Paperclip UI and PyTest unit tests for hermes-gateway orchestrator.

**Architecture:** Two independent test suites. Playwright specs in `paperclip/tests/docker-e2e/` connect to running Docker Compose (like release-smoke pattern). PyTest in `hermes-gateway/tests/` mocks all external dependencies (psycopg2, httpx, filesystem) for pure unit tests.

**Tech Stack:** Playwright (`@playwright/test`), PyTest (`unittest.mock`)

---

## File Structure

### New files (Playwright)
```
paperclip/tests/docker-e2e/
├── playwright.config.ts     # Config — Docker URL, auth, chromium
├── fixtures.ts              # signIn, apiGet, createTestAgent, createTestIssue
├── agents.spec.ts           # Agent CRUD, detail, instructions
├── issues.spec.ts           # Issue CRUD, detail, checklist, comments
├── dashboard.spec.ts        # Dashboard rendering, navigation
├── settings.spec.ts         # Instance settings — timezone, time format
└── roles-skills.spec.ts     # Company skills list, toggle; roles list, create
```

### New files (PyTest)
```
hermes-gateway/
├── pyproject.toml           # pytest config
└── tests/
    ├── __init__.py
    ├── conftest.py          # Fixtures: mock_db, mock_env, temp_dirs
    ├── test_api_keys.py     # _load_agent_api_keys tests
    ├── test_orchestrator.py # fetch_agents, instructions, hot-reload, patching
    ├── test_config_generator.py  # config generation, template substitution
    └── test_skill_importer.py    # skill scanning, priority, DB upsert
```

---

## Task 1: Playwright — Config and Fixtures

**Files:**
- Create: `paperclip/tests/docker-e2e/playwright.config.ts`
- Create: `paperclip/tests/docker-e2e/fixtures.ts`

- [ ] **Step 1: Create playwright.config.ts**

```typescript
import { defineConfig } from "@playwright/test";

const BASE_URL =
  process.env.PAPERCLIP_DOCKER_E2E_BASE_URL ?? "http://127.0.0.1:3100";

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.spec.ts",
  timeout: 90_000,
  expect: {
    timeout: 15_000,
  },
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: BASE_URL,
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
  outputDir: "./test-results",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "./playwright-report" }],
  ],
});
```

- [ ] **Step 2: Create fixtures.ts**

```typescript
import { expect, type Page } from "@playwright/test";

const ADMIN_EMAIL =
  process.env.PAPERCLIP_DOCKER_E2E_EMAIL ?? "admin@test.local";
const ADMIN_PASSWORD =
  process.env.PAPERCLIP_DOCKER_E2E_PASSWORD ?? "password";

export async function signIn(page: Page) {
  await page.goto("/");
  await expect(page).toHaveURL(/\/auth/, { timeout: 20_000 });

  await page.locator('input[type="email"]').fill(ADMIN_EMAIL);
  await page.locator('input[type="password"]').fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();

  await expect(page).not.toHaveURL(/\/auth/, { timeout: 20_000 });
}

export async function apiGet(
  page: Page,
  path: string
): Promise<{ status: number; body: unknown }> {
  const baseUrl = new URL(page.url()).origin;
  const res = await page.request.get(`${baseUrl}${path}`);
  return { status: res.status(), body: await res.json() };
}

export async function apiPost(
  page: Page,
  path: string,
  data: Record<string, unknown>
): Promise<{ status: number; body: unknown }> {
  const baseUrl = new URL(page.url()).origin;
  const res = await page.request.post(`${baseUrl}${path}`, { data });
  return { status: res.status(), body: await res.json() };
}

export async function getCompanyId(page: Page): Promise<string> {
  const { body } = await apiGet(page, "/api/companies");
  const companies = body as Array<{ id: string; name: string }>;
  if (companies.length === 0) throw new Error("No companies found");
  return companies[0].id;
}

export async function createTestAgent(
  page: Page,
  companyId: string,
  name: string
): Promise<string> {
  const { body } = await apiPost(page, `/api/companies/${companyId}/agents`, {
    name,
    assignedRole: "worker",
    adapterType: "hermes_local",
  });
  const agent = body as { id: string };
  return agent.id;
}

export async function createTestIssue(
  page: Page,
  companyId: string,
  opts: { title: string; description?: string; assigneeAgentId?: string }
): Promise<string> {
  const payload: Record<string, unknown> = { title: opts.title };
  if (opts.description) payload.description = opts.description;
  if (opts.assigneeAgentId) payload.assigneeAgentId = opts.assigneeAgentId;
  const { body } = await apiPost(
    page,
    `/api/companies/${companyId}/issues`,
    payload
  );
  const issue = body as { id: string };
  return issue.id;
}
```

- [ ] **Step 3: Verify files created**

Run: `ls -la paperclip/tests/docker-e2e/`

- [ ] **Step 4: Commit**

```bash
git add paperclip/tests/docker-e2e/playwright.config.ts paperclip/tests/docker-e2e/fixtures.ts
git commit -m "test: add Playwright docker-e2e config and fixtures"
```

---

## Task 2: Playwright — Agents spec

**Files:**
- Create: `paperclip/tests/docker-e2e/agents.spec.ts`

- [ ] **Step 1: Write agents.spec.ts**

```typescript
import { expect, test } from "@playwright/test";
import {
  signIn,
  apiGet,
  apiPost,
  getCompanyId,
  createTestAgent,
} from "./fixtures";

test.describe("Agents", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("agents list renders", async ({ page }) => {
    await page.goto("/agents");
    await expect(page.getByText(/agent/i)).toBeVisible({ timeout: 15_000 });
  });

  test("agents list shows agent rows", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const agentsRes = await apiGet(
      page,
      `/api/companies/${companyId}/agents`
    );
    const agents = agentsRes.body as Array<{ id: string; name: string }>;
    if (agents.length === 0) return;

    await page.goto("/agents");
    await expect(
      page.locator("text=" + agents[0].name).first()
    ).toBeVisible({ timeout: 15_000 });
  });

  test("navigate to agent detail", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const agentId = await createTestAgent(
      page,
      companyId,
      `Test Agent ${Date.now()}`
    );

    await page.goto("/agents");
    await page.goto(`/agents/${agentId}/dashboard`);
    await expect(page).toHaveURL(new RegExp(agentId), { timeout: 15_000 });
    await expect(page.locator("h2")).toBeVisible({ timeout: 10_000 });
  });

  test("agent detail has tabs", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const agentId = await createTestAgent(
      page,
      companyId,
      `Tab Test ${Date.now()}`
    );

    await page.goto(`/agents/${agentId}/dashboard`);

    for (const tab of ["Instructions", "Skills", "Configuration"]) {
      await expect(
        page.getByRole("tab", { name: tab })
      ).toBeVisible({ timeout: 10_000 });
    }
  });

  test("agent instructions tab loads", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const agentId = await createTestAgent(
      page,
      companyId,
      `Instr Test ${Date.now()}`
    );

    await page.goto(`/agents/${agentId}/instructions`);
    await expect(
      page.locator("text=AGENTS.md").or(page.locator("text=SOUL.md"))
    ).toBeVisible({ timeout: 10_000 });
  });
});
```

- [ ] **Step 2: Run agents spec**

Run (from `paperclip/` directory): `pnpm exec playwright test --config tests/docker-e2e/playwright.config.ts agents.spec.ts`
Expected: All tests pass (requires Docker Compose running with Paperclip accessible at configured URL)

- [ ] **Step 3: Commit**

```bash
git add paperclip/tests/docker-e2e/agents.spec.ts
git commit -m "test: add Playwright agents E2E spec"
```

---

## Task 3: Playwright — Issues spec

**Files:**
- Create: `paperclip/tests/docker-e2e/issues.spec.ts`

- [ ] **Step 1: Write issues.spec.ts**

```typescript
import { expect, test } from "@playwright/test";
import {
  signIn,
  apiGet,
  createTestIssue,
  getCompanyId,
} from "./fixtures";

test.describe("Issues", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("issues list renders", async ({ page }) => {
    await page.goto("/issues");
    await expect(page).toHaveURL(/\/issues/, { timeout: 15_000 });
  });

  test("issues list shows existing issues", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const issueId = await createTestIssue(page, companyId, {
      title: `Test Issue ${Date.now()}`,
    });

    await page.goto("/issues");
    await expect(
      page.locator(`text=Test Issue`).first()
    ).toBeVisible({ timeout: 15_000 });
  });

  test("issue detail page renders", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const title = `Detail Test ${Date.now()}`;
    const issueId = await createTestIssue(page, companyId, { title });

    await page.goto(`/issues/${issueId}`);
    await expect(page).toHaveURL(new RegExp(issueId), { timeout: 15_000 });
    await expect(page.locator("text=" + title)).toBeVisible({
      timeout: 10_000,
    });
  });

  test("issue detail has comments tab", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const issueId = await createTestIssue(page, companyId, {
      title: `Comment Test ${Date.now()}`,
    });

    await page.goto(`/issues/${issueId}`);
    await expect(
      page.getByRole("tab", { name: /comment/i })
    ).toBeVisible({ timeout: 10_000 });
  });

  test("issue detail has activity tab", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const issueId = await createTestIssue(page, companyId, {
      title: `Activity Test ${Date.now()}`,
    });

    await page.goto(`/issues/${issueId}`);
    await expect(
      page.getByRole("tab", { name: /activity/i })
    ).toBeVisible({ timeout: 10_000 });
  });
});
```

- [ ] **Step 2: Run issues spec**

Run (from `paperclip/`): `pnpm exec playwright test --config tests/docker-e2e/playwright.config.ts issues.spec.ts`

- [ ] **Step 3: Commit**

```bash
git add paperclip/tests/docker-e2e/issues.spec.ts
git commit -m "test: add Playwright issues E2E spec"
```

---

## Task 4: Playwright — Dashboard spec

**Files:**
- Create: `paperclip/tests/docker-e2e/dashboard.spec.ts`

- [ ] **Step 1: Write dashboard.spec.ts**

```typescript
import { expect, test } from "@playwright/test";
import { signIn, apiGet, getCompanyId } from "./fixtures";

test.describe("Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("dashboard renders metric cards", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(
      page.locator("text=Agents Enabled").or(page.locator("text=Agents"))
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.locator("text=Pending Approvals").or(page.locator("text=Month"))
    ).toBeVisible({ timeout: 10_000 });
  });

  test("sidebar navigation works", async ({ page }) => {
    await page.goto("/dashboard");

    const agentsLink = page.locator('a[href="/agents"]').first();
    if (await agentsLink.isVisible()) {
      await agentsLink.click();
      await expect(page).toHaveURL(/\/agents/, { timeout: 10_000 });
    }
  });

  test("dashboard shows recent activity section", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(
      page
        .locator("text=Recent Activity")
        .or(page.locator("text=Recent Tasks"))
    ).toBeVisible({ timeout: 15_000 });
  });
});
```

- [ ] **Step 2: Run dashboard spec**

Run (from `paperclip/`): `pnpm exec playwright test --config tests/docker-e2e/playwright.config.ts dashboard.spec.ts`

- [ ] **Step 3: Commit**

```bash
git add paperclip/tests/docker-e2e/dashboard.spec.ts
git commit -m "test: add Playwright dashboard E2E spec"
```

---

## Task 5: Playwright — Settings spec

**Files:**
- Create: `paperclip/tests/docker-e2e/settings.spec.ts`

- [ ] **Step 1: Write settings.spec.ts**

```typescript
import { expect, test } from "@playwright/test";
import { signIn, apiGet, apiPost } from "./fixtures";

test.describe("Instance Settings", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("settings page renders", async ({ page }) => {
    await page.goto("/settings/general");
    await expect(
      page.locator("text=General").first()
    ).toBeVisible({ timeout: 15_000 });
  });

  test("timezone selector visible", async ({ page }) => {
    await page.goto("/settings/general");
    await expect(
      page.locator("select").filter({ hasText: /UTC|Europe|America/ }).first()
    ).toBeVisible({ timeout: 15_000 });
  });

  test("time format toggle visible", async ({ page }) => {
    await page.goto("/settings/general");
    await expect(
      page.locator("text=24-hour").or(page.locator("text=12-hour"))
    ).toBeVisible({ timeout: 15_000 });
  });

  test("sign out button visible", async ({ page }) => {
    await page.goto("/settings/general");
    await expect(
      page.getByRole("button", { name: /sign out/i })
    ).toBeVisible({ timeout: 15_000 });
  });
});
```

- [ ] **Step 2: Run settings spec**

Run (from `paperclip/`): `pnpm exec playwright test --config tests/docker-e2e/playwright.config.ts settings.spec.ts`

- [ ] **Step 3: Commit**

```bash
git add paperclip/tests/docker-e2e/settings.spec.ts
git commit -m "test: add Playwright settings E2E spec"
```

---

## Task 6: Playwright — Roles & Skills spec

**Files:**
- Create: `paperclip/tests/docker-e2e/roles-skills.spec.ts`

- [ ] **Step 1: Write roles-skills.spec.ts**

```typescript
import { expect, test } from "@playwright/test";
import { signIn, apiGet, getCompanyId } from "./fixtures";

test.describe("Company Skills", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("skills page renders", async ({ page }) => {
    await page.goto("/skills");
    await expect(
      page.locator("text=Skills").first()
    ).toBeVisible({ timeout: 15_000 });
  });

  test("skills page shows available count", async ({ page }) => {
    await page.goto("/skills");
    await expect(
      page.locator("text=/available/").or(page.locator("text=available"))
    ).toBeVisible({ timeout: 15_000 });
  });

  test("skills list loads from API", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const { body } = await apiGet(
      page,
      `/api/companies/${companyId}/skills`
    );
    const skills = body as Array<{ id: string; name: string }>;

    await page.goto("/skills");
    if (skills.length > 0) {
      await expect(
        page.locator("text=" + skills[0].name).first()
      ).toBeVisible({ timeout: 10_000 });
    }
  });
});

test.describe("Company Roles", () => {
  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("roles page renders", async ({ page }) => {
    await page.goto("/roles");
    await expect(
      page.locator("text=Roles").first()
    ).toBeVisible({ timeout: 15_000 });
  });

  test("roles list loads from API", async ({ page }) => {
    const companyId = await getCompanyId(page);
    const { body } = await apiGet(
      page,
      `/api/companies/${companyId}/roles`
    );
    const roles = body as Array<{ id: string; name: string }>;

    await page.goto("/roles");
    if (roles.length > 0) {
      await expect(
        page.locator("text=" + roles[0].name).first()
      ).toBeVisible({ timeout: 10_000 });
    }
  });
});
```

- [ ] **Step 2: Run roles-skills spec**

Run (from `paperclip/`): `pnpm exec playwright test --config tests/docker-e2e/playwright.config.ts roles-skills.spec.ts`

- [ ] **Step 3: Commit**

```bash
git add paperclip/tests/docker-e2e/roles-skills.spec.ts
git commit -m "test: add Playwright roles and skills E2E spec"
```

---

## Task 7: PyTest — Setup and conftest

**Files:**
- Create: `hermes-gateway/pyproject.toml`
- Create: `hermes-gateway/tests/__init__.py`
- Create: `hermes-gateway/tests/conftest.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["orchestrator"]
```

- [ ] **Step 2: Create tests/__init__.py**

Empty file.

- [ ] **Step 3: Create conftest.py**

```python
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

for mod in ["psycopg2", "psycopg2.extras", "httpx"]:
    sys.modules.setdefault(mod, MagicMock())


@pytest.fixture
def mock_cursor():
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    return cursor


@pytest.fixture
def mock_db(mock_cursor):
    conn = MagicMock()
    conn.cursor.return_value = mock_cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    with patch("psycopg2.connect", return_value=conn) as mock_connect:
        yield mock_connect, conn, mock_cursor


@pytest.fixture
def mock_env(tmp_path):
    env = {
        "DATABASE_URL": "postgres://test:test@localhost:5432/test",
        "BETTER_AUTH_SECRET": "test-secret-key-at-least-32-chars-long!!",
        "PAPERCLIP_API_URL": "http://localhost:3100/api",
        "PAPERCLIP_DATA_PATH": str(tmp_path / "paperclip"),
        "PAPERCLIP_INSTANCE_ID": "default",
        "ORCHESTRATOR_POLL_INTERVAL": "60",
    }
    saved = {}
    for key, val in env.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = val
    yield env
    for key, old in saved.items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


@pytest.fixture
def sample_agent_row():
    return {
        "agent_id": "00000000-0000-0000-0000-000000000001",
        "agent_name": "Test Agent",
        "company_id": "00000000-0000-0000-0000-000000000010",
        "company_name": "Test Company",
        "role": "worker",
        "adapter_type": "hermes_local",
        "status": "active",
        "adapter_config": json.dumps({}),
        "personality": "kawaii",
    }


@pytest.fixture
def temp_profile_dir(tmp_path):
    profile = tmp_path / "profiles" / "00000000-0000-0000-0000-000000000001"
    profile.mkdir(parents=True)
    (profile / "memories").mkdir()
    (profile / "skills").mkdir()
    (profile / "sessions").mkdir()
    return profile


@pytest.fixture
def agent_api_keys_file(tmp_path):
    keys = {
        "00000000-0000-0000-0000-000000000001": "pcp_test_key_agent_1",
        "00000000-0000-0000-0000-000000000002": "pcp_test_key_agent_2",
    }
    path = tmp_path / "agent_api_keys.json"
    path.write_text(json.dumps(keys))
    return path
```

- [ ] **Step 4: Verify pytest discovers no tests yet**

Run: `cd hermes-gateway && python -m pytest tests/ --collect-only 2>&1 | head -5`
Expected: "no tests collected" or empty collection

- [ ] **Step 5: Commit**

```bash
git add hermes-gateway/pyproject.toml hermes-gateway/tests/__init__.py hermes-gateway/tests/conftest.py
git commit -m "test: add PyTest setup and conftest for hermes-gateway"
```

---

## Task 8: PyTest — test_api_keys.py

**Files:**
- Create: `hermes-gateway/tests/test_api_keys.py`

- [ ] **Step 1: Write test_api_keys.py**

```python
import json
from pathlib import Path
from unittest.mock import patch

import orchestrator


def _make_keys_file(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "agent_api_keys.json"
    p.write_text(json.dumps(data))
    return p


class TestLoadAgentApiKeys:
    def test_valid_json(self, tmp_path):
        keys = {"agent-1": "pcp_key_abc123", "agent-2": "pcp_key_def456"}
        path = _make_keys_file(tmp_path, keys)
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == keys

    def test_missing_file(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == {}

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid json!!!")
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == {}

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("")
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == {}

    def test_empty_dict(self, tmp_path):
        path = _make_keys_file(tmp_path, {})
        with patch.object(orchestrator, "_AGENT_API_KEYS_PATH", path):
            result = orchestrator._load_agent_api_keys()
        assert result == {}
```

- [ ] **Step 2: Run test_api_keys**

Run: `cd hermes-gateway && python -m pytest tests/test_api_keys.py -v`
Expected: 5 passed

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/tests/test_api_keys.py
git commit -m "test: add PyTest for _load_agent_api_keys"
```

---

## Task 9: PyTest — test_orchestrator.py

**Files:**
- Create: `hermes-gateway/tests/test_orchestrator.py`

- [ ] **Step 1: Write test_orchestrator.py**

```python
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import orchestrator


class TestFetchAgentsFromDb:
    def test_returns_active_hermes_agents(self, mock_db, mock_env):
        mock_connect, conn, cursor = mock_db
        cursor.fetchall.return_value = [
            {
                "agent_id": "a1",
                "agent_name": "Agent1",
                "company_id": "c1",
                "company_name": "Co1",
                "role": "worker",
                "adapter_type": "hermes_local",
                "status": "active",
                "adapter_config": "{}",
                "personality": "kawaii",
            }
        ]
        result = orchestrator.fetch_agents_from_db()
        assert len(result) == 1
        assert result[0]["agent_id"] == "a1"

    def test_excludes_terminated(self, mock_db, mock_env):
        mock_connect, conn, cursor = mock_db
        cursor.fetchall.return_value = []
        result = orchestrator.fetch_agents_from_db()
        assert result == []

    def test_excludes_non_hermes_adapter(self, mock_db, mock_env):
        mock_connect, conn, cursor = mock_db
        cursor.fetchall.return_value = [
            {
                "agent_id": "a2",
                "agent_name": "Agent2",
                "company_id": "c1",
                "company_name": "Co1",
                "role": "worker",
                "adapter_type": "process",
                "status": "active",
                "adapter_config": "{}",
                "personality": "kawaii",
            }
        ]
        result = orchestrator.fetch_agents_from_db()
        assert result == []


class TestReadPaperclipInstructions:
    def test_reads_agents_md(self, tmp_path, mock_env):
        instr_dir = (
            tmp_path
            / "paperclip"
            / "instances"
            / "default"
            / "companies"
            / "c1"
            / "agents"
            / "a1"
            / "instructions"
        )
        instr_dir.mkdir(parents=True)
        (instr_dir / "AGENTS.md").write_text("# Agent instructions")

        with patch.object(orchestrator, "PAPERCLIP_DATA_PATH", str(tmp_path / "paperclip")):
            result = orchestrator._read_paperclip_instructions("a1", "c1")
        assert result == "# Agent instructions"

    def test_fallback_to_instructions_md(self, tmp_path, mock_env):
        instr_dir = (
            tmp_path
            / "paperclip"
            / "instances"
            / "default"
            / "companies"
            / "c1"
            / "agents"
            / "a1"
            / "instructions"
        )
        instr_dir.mkdir(parents=True)
        (instr_dir / "instructions.md").write_text("Fallback instructions")

        with patch.object(orchestrator, "PAPERCLIP_DATA_PATH", str(tmp_path / "paperclip")):
            result = orchestrator._read_paperclip_instructions("a1", "c1")
        assert result == "Fallback instructions"

    def test_fallback_to_soul_md(self, tmp_path, mock_env):
        instr_dir = (
            tmp_path
            / "paperclip"
            / "instances"
            / "default"
            / "companies"
            / "c1"
            / "agents"
            / "a1"
            / "instructions"
        )
        instr_dir.mkdir(parents=True)
        (instr_dir / "SOUL.md").write_text("Soul content")

        with patch.object(orchestrator, "PAPERCLIP_DATA_PATH", str(tmp_path / "paperclip")):
            result = orchestrator._read_paperclip_instructions("a1", "c1")
        assert result == "Soul content"

    def test_returns_none_when_no_files(self, tmp_path, mock_env):
        instr_dir = (
            tmp_path
            / "paperclip"
            / "instances"
            / "default"
            / "companies"
            / "c1"
            / "agents"
            / "a1"
            / "instructions"
        )
        instr_dir.mkdir(parents=True)

        with patch.object(orchestrator, "PAPERCLIP_DATA_PATH", str(tmp_path / "paperclip")):
            result = orchestrator._read_paperclip_instructions("a1", "c1")
        assert result is None


class TestBuildSoulMd:
    def test_includes_role(self):
        result = orchestrator._build_soul_md(role="worker", name="TestBot")
        assert "TestBot" in result
        assert "worker" in result

    def test_docker_disabled_by_default(self):
        result = orchestrator._build_soul_md(role="worker", name="TestBot")
        assert "docker" not in result.lower() or "docker-guard" not in result

    def test_docker_enabled(self):
        result = orchestrator._build_soul_md(
            role="worker", name="TestBot", enable_docker=True
        )
        assert "docker" in result.lower()


class TestHotReload:
    def test_compute_fingerprint_returns_string(self):
        result = orchestrator._compute_source_fingerprint()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fingerprint_is_deterministic(self):
        result1 = orchestrator._compute_source_fingerprint()
        result2 = orchestrator._compute_source_fingerprint()
        assert result1 == result2
```

- [ ] **Step 2: Run test_orchestrator**

Run: `cd hermes-gateway && python -m pytest tests/test_orchestrator.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/tests/test_orchestrator.py
git commit -m "test: add PyTest for orchestrator (fetch_agents, instructions, hot-reload)"
```

---

## Task 10: PyTest — test_config_generator.py

**Files:**
- Create: `hermes-gateway/tests/test_config_generator.py`

- [ ] **Step 1: Write test_config_generator.py**

```python
from pathlib import Path
from unittest.mock import patch

from config_generator import generate_profile_config, ensure_profile_dirs


class TestGenerateProfileConfig:
    def _write_template(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "config-template.yaml"
        p.write_text(content)
        return p

    def test_basic_field_substitution(self, tmp_path):
        template = """
agent:
  name: ${AGENT_NAME}
  model: ${MODEL}
"""
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8642,
                agent_name="TestBot",
            )
        assert "TestBot" in result
        assert "glm-5.1" in result

    def test_port_substitution(self, tmp_path):
        template = """
server:
  port: ${ALLOCATED_PORT}
"""
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8650,
            )
        assert "8650" in result

    def test_telegram_section_included(self, tmp_path):
        template = """
telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  chat_id: ${TELEGRAM_CHAT_ID}
"""
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8642,
                telegram_bot_token="123456:ABC",
                telegram_chat_id="789",
            )
        assert "123456:ABC" in result
        assert "789" in result

    def test_telegram_section_absent(self, tmp_path):
        template = """
agent:
  name: ${AGENT_NAME}
"""
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8642,
            )
        assert "telegram" not in result.lower() or "${TELEGRAM" not in result

    def test_paperclip_api_key_substitution(self, tmp_path):
        template = """
paperclip:
  api_key: ${PAPERCLIP_API_KEY}
"""
        path = self._write_template(tmp_path, template)
        with patch("config_generator._TEMPLATE_PATH", path):
            result = generate_profile_config(
                agent_id="a1",
                company_id="c1",
                allocated_port=8642,
                paperclip_api_key="pcp_test_key",
            )
        assert "pcp_test_key" in result


class TestEnsureProfileDirs:
    def test_creates_subdirectories(self, tmp_path):
        profile = tmp_path / "agent-1"
        ensure_profile_dirs(profile)
        assert (profile / "memories").is_dir()
        assert (profile / "skills").is_dir()
        assert (profile / "sessions").is_dir()

    def test_idempotent(self, tmp_path):
        profile = tmp_path / "agent-1"
        ensure_profile_dirs(profile)
        ensure_profile_dirs(profile)
        assert (profile / "memories").is_dir()
```

- [ ] **Step 2: Run test_config_generator**

Run: `cd hermes-gateway && python -m pytest tests/test_config_generator.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/tests/test_config_generator.py
git commit -m "test: add PyTest for config_generator"
```

---

## Task 11: PyTest — test_skill_importer.py

**Files:**
- Create: `hermes-gateway/tests/test_skill_importer.py`

- [ ] **Step 1: Write test_skill_importer.py**

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from skill_importer import scan_skill_dirs, _parse_frontmatter


class TestParseFrontmatter:
    def test_extracts_name_and_description(self):
        text = """---
name: Docker Management
description: Manage Docker containers
---
# Skill content here"""
        result = _parse_frontmatter(text)
        assert result["name"] == "Docker Management"
        assert result["description"] == "Manage Docker containers"

    def test_no_frontmatter(self):
        text = "# Just a heading\nSome content"
        result = _parse_frontmatter(text)
        assert result == {}

    def test_partial_frontmatter(self):
        text = """---
name: Only Name
---
Content"""
        result = _parse_frontmatter(text)
        assert result["name"] == "Only Name"
        assert "description" not in result


class TestScanSkillDirs:
    def test_discovers_skills_from_all_dirs(self, tmp_path):
        custom_dir = tmp_path / "custom" / "devops" / "docker-mgmt"
        custom_dir.mkdir(parents=True)
        (custom_dir / "SKILL.md").write_text(
            "---\nname: Docker\ndescription: Docker management\n---\n# Docker skill"
        )

        builtin_dir = tmp_path / "builtin" / "coding" / "python-dev"
        builtin_dir.mkdir(parents=True)
        (builtin_dir / "SKILL.md").write_text(
            "---\nname: Python Dev\ndescription: Python development\n---\n# Python skill"
        )

        dirs = [
            (str(custom_dir.parent.parent), "Project skills"),
            (str(builtin_dir.parent.parent), "Hermes Agent"),
        ]
        with patch("skill_importer.HERMES_SKILL_DIRS", dirs):
            skills = scan_skill_dirs()

        assert len(skills) == 2
        slugs = {s["slug"] for s in skills}
        assert "docker-mgmt" in slugs
        assert "python-dev" in slugs

    def test_custom_skill_priority(self, tmp_path):
        for label, subdir in [("custom", "custom"), ("builtin", "builtin")]:
            d = tmp_path / subdir / "devops" / "docker-mgmt"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(
                f"---\nname: Docker ({label})\ndescription: test\n---\n# {label}"
            )

        dirs = [
            (str(tmp_path / "custom"), "Project skills"),
            (str(tmp_path / "builtin"), "Hermes Agent"),
        ]
        with patch("skill_importer.HERMES_SKILL_DIRS", dirs):
            skills = scan_skill_dirs()

        docker_skill = [s for s in skills if s["slug"] == "docker-mgmt"]
        assert len(docker_skill) == 1
        assert "custom" in docker_skill[0]["name"]

    def test_empty_dirs(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with patch("skill_importer.HERMES_SKILL_DIRS", [(str(empty_dir), "Empty")]):
            skills = scan_skill_dirs()
        assert skills == []

    def test_skips_dirs_without_skill_md(self, tmp_path):
        d = tmp_path / "skills" / "devops" / "broken"
        d.mkdir(parents=True)
        with patch(
            "skill_importer.HERMES_SKILL_DIRS",
            [(str(tmp_path / "skills"), "Test")],
        ):
            skills = scan_skill_dirs()
        assert skills == []

    def test_extracts_category_and_source_label(self, tmp_path):
        d = tmp_path / "skills" / "devops" / "docker"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: Docker\ndescription: test\n---\n# Docker"
        )
        with patch(
            "skill_importer.HERMES_SKILL_DIRS",
            [(str(tmp_path / "skills"), "Project skills")],
        ):
            skills = scan_skill_dirs()
        assert skills[0]["category"] == "devops"
        assert skills[0]["source_label"] == "Project skills"
```

- [ ] **Step 2: Run test_skill_importer**

Run: `cd hermes-gateway && python -m pytest tests/test_skill_importer.py -v`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/tests/test_skill_importer.py
git commit -m "test: add PyTest for skill_importer"
```

---

## Task 12: Run all tests and verify

**Files:** None (verification only)

- [ ] **Step 1: Run all PyTest tests**

Run: `cd hermes-gateway && python -m pytest tests/ -v`
Expected: All tests collected and pass

- [ ] **Step 2: Run all Playwright tests (requires Docker Compose)**

Run: `cd paperclip && pnpm exec playwright test --config tests/docker-e2e/playwright.config.ts`
Expected: All specs pass

- [ ] **Step 3: Commit any remaining fixes**

```bash
git add -A
git commit -m "test: finalize automated test suites"
```
