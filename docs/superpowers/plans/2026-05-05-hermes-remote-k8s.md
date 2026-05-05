# Hermes Remote K8s Adapter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Paperclip agents to run as individual Kubernetes Pods on remote clusters, managed via k8s API with HTTPS communication.

**Architecture:** New `hermes_remote` adapter type in Paperclip server creates/manages k8s Deployments directly via k8s API. Each agent runs in its own Pod with `api_server.py` as the entrypoint. An operator service runs in k8s to keep agent Pods in sync with the Paperclip DB. MCP servers are exposed via Traefik HTTPS for remote agent access.

**Tech Stack:** TypeScript (Paperclip adapter), Python (operator, agent entrypoint), k8s API (Deployment/Service/ConfigMap/Secret management), Docker (agent image).

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `paperclip/server/src/adapters/hermes_remote/index.ts` | Adapter registration |
| `paperclip/server/src/adapters/hermes_remote/execute.ts` | Main execute: k8s provision + SSE streaming |
| `paperclip/server/src/adapters/hermes_remote/k8s-client.ts` | K8s API wrapper (apply/delete Deployment, Service, ConfigMap, Secret) |
| `paperclip/server/src/adapters/hermes_remote/config-builder.ts` | Generate config.yaml, SOUL.md for agent Pod |
| `paperclip/server/src/adapters/hermes_remote/types.ts` | HermesRemoteConfig interface, k8s resource types |
| `hermes-agent-image/Dockerfile` | Agent Pod container image |
| `hermes-agent-image/entrypoint.py` | Pod startup: env loading, config substitution, api_server launch |
| `agent-operator/Dockerfile` | Operator container image |
| `agent-operator/main.py` | FastAPI app with health/metrics + reconcile background task |
| `agent-operator/reconciler.py` | Poll DB → CRUD k8s Deployments |
| `agent-operator/k8s_resources.py` | Generate k8s manifests (Deployment, Service, ConfigMap, Secret) |
| `agent-operator/config.py` | Operator configuration (DB URL, namespace, image) |
| `k8s/namespace.yaml` | Target namespace `agents` |
| `k8s/rbac.yaml` | ServiceAccount + Role + RoleBinding for operator |
| `k8s/agent-operator.yaml` | Operator Deployment |
| `k8s/networkpolicy.yaml` | Default NetworkPolicy for agent Pods |

### Modified Files

| File | Change |
|------|--------|
| `paperclip/server/src/adapters/registry.ts` | Register `hermesRemoteAdapter` |
| `paperclip/server/src/adapters/builtin-adapter-types.ts` | Add `"hermes_remote"` to set |
| `docker-compose.yml` | Add Traefik labels for MCP HTTPS endpoints |

---

## Task 1: Types and Config Schema

**Files:**
- Create: `paperclip/server/src/adapters/hermes_remote/types.ts`

- [ ] **Step 1: Create types.ts with config interface and k8s resource types**

```typescript
// paperclip/server/src/adapters/hermes_remote/types.ts

export interface HermesRemoteConfig {
  k8sApiUrl: string;
  k8sNamespace: string;
  k8sToken: string;
  k8sCAData?: string;

  agentImage: string;
  imagePullSecret?: string;

  resources?: {
    cpu: string;
    memory: string;
  };

  mcpEndpoints?: {
    paperclip?: string;
    rag?: string;
    memory?: string;
    outline?: string;
  };

  providerKeys?: Record<string, string>;

  timeoutSec?: number;
}

export interface K8sDeployment {
  apiVersion: "apps/v1";
  kind: "Deployment";
  metadata: {
    name: string;
    namespace: string;
    labels: Record<string, string>;
  };
  spec: {
    replicas: number;
    selector: { matchLabels: Record<string, string> };
    template: {
      metadata: { labels: Record<string, string> };
      spec: {
        containers: Array<{
          name: string;
          image: string;
          ports: Array<{ containerPort: number }>;
          envFrom: Array<{ secretRef: { name: string } }>;
          volumeMounts: Array<{ name: string; mountPath: string }>;
          resources?: { limits: Record<string, string>; requests: Record<string, string> };
        }>;
        volumes: Array<{
          name: string;
          configMap: { name: string };
        }>;
        imagePullSecrets?: Array<{ name: string }>;
      };
    };
  };
}

export interface K8sService {
  apiVersion: "v1";
  kind: "Service";
  metadata: { name: string; namespace: string; labels: Record<string, string> };
  spec: {
    selector: Record<string, string>;
    ports: Array<{ port: number; targetPort: number }>;
    type: string;
  };
}

export interface K8sConfigMap {
  apiVersion: "v1";
  kind: "ConfigMap";
  metadata: { name: string; namespace: string; labels: Record<string, string> };
  data: Record<string, string>;
}

export interface K8sSecret {
  apiVersion: "v1";
  kind: "Secret";
  metadata: { name: string; namespace: string; labels: Record<string, string> };
  type: "Opaque";
  stringData: Record<string, string>;
}

export interface SSEEvent {
  event?: string;
  data: Record<string, unknown>;
}

export function agentResourceName(agentId: string): string {
  return `agent-${agentId.slice(0, 8)}`;
}
```

- [ ] **Step 2: Commit**

```bash
git add paperclip/server/src/adapters/hermes_remote/types.ts
git commit -m "feat(hermes-remote): add types and config schema"
```

---

## Task 2: K8s API Client

**Files:**
- Create: `paperclip/server/src/adapters/hermes_remote/k8s-client.ts`

- [ ] **Step 1: Create k8s-client.ts with CRUD operations via raw HTTPS**

This uses Node.js `fetch` to call the k8s API directly (no external dependencies needed). The k8s API supports `?fieldManager=paperclip&force=true` for server-side apply.

```typescript
// paperclip/server/src/adapters/hermes_remote/k8s-client.ts
import type { HermesRemoteConfig, K8sDeployment, K8sService, K8sConfigMap, K8sSecret } from "./types.js";

export class K8sClient {
  private baseUrl: string;
  private token: string;
  private caData: string | undefined;
  private namespace: string;

  constructor(config: HermesRemoteConfig) {
    this.baseUrl = config.k8sApiUrl.replace(/\/+$/, "");
    this.token = config.k8sToken;
    this.caData = config.k8sCAData;
    this.namespace = config.k8sNamespace;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
      "Content-Type": "application/json",
    };

    const res = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (res.status === 409 && method === "POST") {
      throw new Error(`K8s resource already exists: ${path}`);
    }
    if (res.status === 404 && method === "GET") {
      return null as T;
    }
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`K8s API error ${res.status}: ${text.slice(0, 500)}`);
    }
    if (res.status === 204) return undefined as T;
    return res.json() as Promise<T>;
  }

  async applyConfigMap(name: string, data: Record<string, string>): Promise<void> {
    const body: K8sConfigMap = {
      apiVersion: "v1",
      kind: "ConfigMap",
      metadata: { name, namespace: this.namespace, labels: { "managed-by": "paperclip" } },
      data,
    };
    await this.request("PUT", `/api/v1/namespaces/${this.namespace}/configmaps/${name}`, body).catch(async () => {
      await this.request("POST", `/api/v1/namespaces/${this.namespace}/configmaps`, body);
    });
  }

  async applySecret(name: string, stringData: Record<string, string>): Promise<void> {
    const body: K8sSecret = {
      apiVersion: "v1",
      kind: "Secret",
      metadata: { name, namespace: this.namespace, labels: { "managed-by": "paperclip" } },
      type: "Opaque",
      stringData,
    };
    await this.request("PUT", `/api/v1/namespaces/${this.namespace}/secrets/${name}`, body).catch(async () => {
      await this.request("POST", `/api/v1/namespaces/${this.namespace}/secrets`, body);
    });
  }

  async applyDeployment(name: string, deployment: K8sDeployment): Promise<void> {
    await this.request("PUT", `/apis/apps/v1/namespaces/${this.namespace}/deployments/${name}`, deployment).catch(
      async () => {
        await this.request("POST", `/apis/apps/v1/namespaces/${this.namespace}/deployments`, deployment);
      }
    );
  }

  async applyService(name: string, service: K8sService): Promise<void> {
    await this.request("PUT", `/api/v1/namespaces/${this.namespace}/services/${name}`, service).catch(async () => {
      await this.request("POST", `/api/v1/namespaces/${this.namespace}/services`, service);
    });
  }

  async isDeploymentReady(name: string): Promise<boolean> {
    const dep = await this.request<{
      status: { readyReplicas?: number; replicas?: number };
    }>("GET", `/apis/apps/v1/namespaces/${this.namespace}/deployments/${name}`);
    if (!dep) return false;
    return (dep.status?.readyReplicas ?? 0) >= 1;
  }

  async deleteDeployment(name: string): Promise<void> {
    await this.request("DELETE", `/apis/apps/v1/namespaces/${this.namespace}/deployments/${name}`).catch(() => {});
  }

  async deleteService(name: string): Promise<void> {
    await this.request("DELETE", `/api/v1/namespaces/${this.namespace}/services/${name}`).catch(() => {});
  }

  async deleteConfigMap(name: string): Promise<void> {
    await this.request("DELETE", `/api/v1/namespaces/${this.namespace}/configmaps/${name}`).catch(() => {});
  }

  async deleteSecret(name: string): Promise<void> {
    await this.request("DELETE", `/api/v1/namespaces/${this.namespace}/secrets/${name}`).catch(() => {});
  }

  getNamespace(): string {
    return this.namespace;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add paperclip/server/src/adapters/hermes_remote/k8s-client.ts
git commit -m "feat(hermes-remote): add k8s API client"
```

---

## Task 3: Config Builder

**Files:**
- Create: `paperclip/server/src/adapters/hermes_remote/config-builder.ts`

- [ ] **Step 1: Create config-builder.ts**

Generates the hermes-agent `config.yaml` for remote deployment. Uses the same structure as `hermes-gateway/config-template.yaml` but with HTTPS MCP URLs instead of internal Docker DNS.

```typescript
// paperclip/server/src/adapters/hermes_remote/config-builder.ts
import type { HermesRemoteConfig } from "./types.js";

export function buildRemoteConfigYaml(config: HermesRemoteConfig, opts: {
  agentId: string;
  companyId: string;
  paperclipApiKey: string;
  agentName: string;
}): string {
  const mcp = config.mcpEndpoints ?? {};
  const paperclipUrl = mcp.paperclip ?? "http://localhost:8082/mcp";
  const ragUrl = mcp.rag ?? "http://localhost:8081/mcp";
  const memoryUrl = mcp.memory ?? "http://localhost:8680/mcp";
  const outlineUrl = mcp.outline ?? "https://outline.collaborationism.tech/mcp";

  return `model:
  default: glm-5.1
  provider: zai
agent:
  tool_use_enforcement: auto
  max_turns: 90
  reasoning_effort: "none"
terminal:
  backend: local
  cwd: .
  timeout: 180
  persistent_shell: true
compression:
  enabled: true
  threshold: 0.85
  target_ratio: 0.2
  protect_last_n: 20
  summary_model: glm-5
  summary_provider: auto
auxiliary:
  vision:
    provider: zai
    model: glm-4.6v
  web_extract:
    provider: auto
    timeout: 360
  session_search:
    provider: auto
    timeout: 30
display:
  compact: true
  personality: kawaii
  streaming: false
  tool_progress: result
memory:
  memory_enabled: true
  user_profile_enabled: true
  nudge_interval: 10
  memory_char_limit: 8000
approvals:
  mode: off
  timeout: 60
web:
  backend: parallel
mcp_servers:
  rag:
    url: ${ragUrl}
    enabled: true
    timeout: 120
    connect_timeout: 60
  paperclip:
    url: ${paperclipUrl}
    headers:
      X-Paperclip-Api-Key: "${opts.paperclipApiKey}"
      X-Paperclip-Company-Id: "${opts.companyId}"
      X-Paperclip-Agent-Id: "${opts.agentId}"
    enabled: true
    timeout: 60
    connect_timeout: 30
  outline:
    url: ${outlineUrl}
    enabled: true
    timeout: 120
    connect_timeout: 60
  memory:
    url: ${memoryUrl}
    enabled: true
    timeout: 30
    connect_timeout: 10
_config_version: 12
security:
  redact_secrets: true
`;
}

export function buildSoulMd(role: string, name: string): string {
  const outlineGuidance = `Outline (knowledge base):
- Use mcp_outline_search to search existing documents before creating new ones.
- Use mcp_outline_create_document to create documents. Always search first to avoid duplicates.
- After creating, use mcp_outline_search to verify the document was created.`;

  const paperclipGuidance = `Paperclip (task management):
- Use mcp_paperclip_paperclip_list_issues to see your tasks.
- Use mcp_paperclip_paperclip_update_issue to update task status.
- Use mcp_paperclip_paperclip_set_checklist to set task checklist.
- Tool names have mcp_paperclip_ prefix (double prefix is correct).`;

  if (role === "ceo" || role === "cto") {
    return `You are ${name}, a managing agent in a remote environment.
${outlineGuidance}
${paperclipGuidance}`;
  }
  return `You are ${name}, a worker agent running in a remote Kubernetes Pod.
${outlineGuidance}
${paperclipGuidance}`;
}

export function buildEnvFile(config: HermesRemoteConfig, opts: {
  paperclipApiUrl: string;
  paperclipApiKey: string;
  hermesApiServerKey: string;
  outlineApiKey: string;
}): string {
  const lines = [
    `PAPERCLIP_API_URL=${opts.paperclipApiUrl}`,
    `PAPERCLIP_RUN_API_KEY=${opts.paperclipApiKey}`,
    `HERMES_API_SERVER_KEY=${opts.hermesApiServerKey}`,
    `OUTLINE_API_KEY=${opts.outlineApiKey}`,
  ];
  const keys = config.providerKeys ?? {};
  for (const [k, v] of Object.entries(keys)) {
    lines.push(`${k}=${v}`);
  }
  return lines.join("\n");
}
```

- [ ] **Step 2: Commit**

```bash
git add paperclip/server/src/adapters/hermes_remote/config-builder.ts
git commit -m "feat(hermes-remote): add config builder for remote agent pods"
```

---

## Task 4: Execute Function (SSE + k8s Provisioning)

**Files:**
- Create: `paperclip/server/src/adapters/hermes_remote/execute.ts`

- [ ] **Step 1: Create execute.ts**

This is the core of the adapter. It:
1. Creates/updates k8s resources (Deployment, Service, ConfigMap, Secret)
2. Waits for Pod readiness
3. POSTs to `/v1/runs` on the agent Pod
4. Reads SSE stream and returns `AdapterExecutionResult`

The SSE parsing reuses the same event format as `hermes-paperclip-adapter/src/server/execute.ts`.

```typescript
// paperclip/server/src/adapters/hermes_remote/execute.ts
import type { AdapterExecutionContext, AdapterExecutionResult } from "../types.js";
import { K8sClient } from "./k8s-client.js";
import { buildRemoteConfigYaml, buildSoulMd, buildEnvFile } from "./config-builder.js";
import { agentResourceName } from "./types.js";
import type { HermesRemoteConfig, K8sDeployment, K8sService } from "./types.js";

function parseConfig(raw: Record<string, unknown>): HermesRemoteConfig {
  return {
    k8sApiUrl: String(raw.k8sApiUrl ?? ""),
    k8sNamespace: String(raw.k8sNamespace ?? "agents"),
    k8sToken: String(raw.k8sToken ?? ""),
    k8sCAData: raw.k8sCAData ? String(raw.k8sCAData) : undefined,
    agentImage: String(raw.agentImage ?? "hermes-agent-remote:latest"),
    imagePullSecret: raw.imagePullSecret ? String(raw.imagePullSecret) : undefined,
    resources: raw.resources as { cpu: string; memory: string } | undefined,
    mcpEndpoints: raw.mcpEndpoints as HermesRemoteConfig["mcpEndpoints"],
    providerKeys: raw.providerKeys as Record<string, string>,
    timeoutSec: Number(raw.timeoutSec) || 3600,
  };
}

export async function execute(ctx: AdapterExecutionContext): Promise<AdapterExecutionResult> {
  const config = parseConfig(ctx.config as Record<string, unknown>);
  if (!config.k8sApiUrl) throw new Error("hermes_remote adapter missing k8sApiUrl");
  if (!config.k8sToken) throw new Error("hermes_remote adapter missing k8sToken");

  const k8s = new K8sClient(config);
  const agentId = ctx.agent.id;
  const companyId = ctx.agent.companyId;
  const name = agentResourceName(agentId);
  const ns = k8s.getNamespace();
  const agentName = ctx.agent.name ?? "Agent";
  const role = (ctx.agent as Record<string, unknown>).role as string ?? "general";

  const apiKey = ctx.authToken ?? "";
  const paperclipApiUrl = process.env.PAPERCLIP_API_URL ?? "http://paperclip-server:3100/api";
  const hermesApiKey = process.env.HERMES_API_SERVER_KEY ?? "";
  const outlineApiKey = process.env.MCP_OUTLINE_API_KEY ?? "";

  const configYaml = buildRemoteConfigYaml(config, {
    agentId,
    companyId,
    paperclipApiKey: apiKey,
    agentName,
  });
  const soulMd = buildSoulMd(role, agentName);
  const envFile = buildEnvFile(config, {
    paperclipApiUrl,
    paperclipApiKey: apiKey,
    hermesApiServerKey: hermesApiKey,
    outlineApiKey,
  });

  await k8s.applyConfigMap(`${name}-config`, {
    "config.yaml": configYaml,
    "SOUL.md": soulMd,
    ".env": envFile,
  });

  await k8s.applySecret(`${name}-secrets`, {
    PAPERCLIP_API_KEY: apiKey,
    ...(config.providerKeys ?? {}),
  });

  const resources = config.resources ?? { cpu: "1", memory: "2Gi" };
  const deployment: K8sDeployment = {
    apiVersion: "apps/v1",
    kind: "Deployment",
    metadata: {
      name,
      namespace: ns,
      labels: { app: "hermes-agent", "agent-id": agentId, "managed-by": "paperclip" },
    },
    spec: {
      replicas: 1,
      selector: { matchLabels: { "agent-id": agentId } },
      template: {
        metadata: { labels: { app: "hermes-agent", "agent-id": agentId, "managed-by": "paperclip" } },
        spec: {
          containers: [{
            name: "agent",
            image: config.agentImage,
            ports: [{ containerPort: 8642 }],
            envFrom: [{ secretRef: { name: `${name}-secrets` } }],
            volumeMounts: [{ name: "config", mountPath: "/etc/hermes" }],
            resources: {
              requests: { cpu: resources.cpu, memory: resources.memory },
              limits: { cpu: resources.cpu, memory: resources.memory },
            },
          }],
          volumes: [{ name: "config", configMap: { name: `${name}-config` } }],
          ...(config.imagePullSecret ? { imagePullSecrets: [{ name: config.imagePullSecret }] } : {}),
        },
      },
    },
  };
  await k8s.applyDeployment(name, deployment);

  const service: K8sService = {
    apiVersion: "v1",
    kind: "Service",
    metadata: { name, namespace: ns, labels: { "managed-by": "paperclip" } },
    spec: {
      selector: { "agent-id": agentId },
      ports: [{ port: 8642, targetPort: 8642 }],
      type: "ClusterIP",
    },
  };
  await k8s.applyService(name, service);

  const readyTimeout = Date.now() + 120_000;
  while (Date.now() < readyTimeout) {
    if (await k8s.isDeploymentReady(name)) break;
    await new Promise((r) => setTimeout(r, 3_000));
  }
  if (!(await k8s.isDeploymentReady(name))) {
    return {
      exitCode: 1,
      signal: null,
      timedOut: false,
      summary: `Agent pod ${name} not ready after 120s`,
      resultJson: { summary: `Agent pod ${name} not ready after 120s` },
    };
  }

  const agentUrl = `http://${name}.${ns}:8642`;

  const input = buildInputMessage(ctx);
  const instructions = soulMd;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), (config.timeoutSec ?? 3600) * 1000);

  try {
    const runRes = await fetch(`${agentUrl}/v1/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input,
        instructions,
        paperclip_api_key: apiKey,
        heartbeat_run_id: ctx.runId,
      }),
      signal: controller.signal,
    });

    if (!runRes.ok) {
      const errText = await runRes.text();
      return {
        exitCode: 1,
        signal: null,
        timedOut: false,
        summary: `Agent POST /v1/runs failed: ${runRes.status} ${errText.slice(0, 300)}`,
        resultJson: { summary: `Agent error: ${runRes.status}` },
      };
    }

    const { run_id: runId } = (await runRes.json()) as { run_id: string; status: string };
    if (!runId) {
      return {
        exitCode: 1,
        signal: null,
        timedOut: false,
        summary: "Agent returned no run_id",
        resultJson: { summary: "Agent returned no run_id" },
      };
    }

    return await streamSSEEvents(agentUrl, runId, controller);
  } finally {
    clearTimeout(timer);
  }
}

function buildInputMessage(ctx: AdapterExecutionContext): string {
  const agentName = ctx.agent.name ?? "Agent";
  const runId = ctx.runId;
  const parts = [`[HEARTBEAT RUN ${runId}]`];
  parts.push(`You are ${agentName}, an AI agent.`);
  if (ctx.context?.taskId) {
    parts.push(`Your assigned task ID: ${ctx.context.taskId}`);
  }
  if (ctx.context?.wakeReason) {
    parts.push(`Wake reason: ${ctx.context.wakeReason}`);
  }
  parts.push("Check your assigned issues with mcp_paperclip_paperclip_list_issues, work on the highest priority task, update the checklist, and report results.");
  return parts.join("\n");
}

async function streamSSEEvents(
  agentUrl: string,
  runId: string,
  controller: AbortController
): Promise<AdapterExecutionResult> {
  const eventsUrl = `${agentUrl}/v1/runs/${runId}/events`;
  const eventsRes = await fetch(eventsUrl, {
    headers: { Accept: "text/event-stream" },
    signal: controller.signal,
  });

  if (!eventsRes.ok) {
    return {
      exitCode: 1,
      signal: null,
      timedOut: false,
      summary: `SSE stream failed: ${eventsRes.status}`,
      resultJson: { summary: `SSE stream failed: ${eventsRes.status}` },
    };
  }

  const body = eventsRes.body;
  if (!body) {
    return {
      exitCode: 1,
      signal: null,
      timedOut: false,
      summary: "No SSE body",
      resultJson: { summary: "No SSE body" },
    };
  }

  let finalText = "";
  let toolCount = 0;
  let usage: Record<string, unknown> | null = null;
  let runFailed = false;
  let errorMsg = "";
  const decoder = new TextDecoder();
  const reader = body.getReader();

  try {
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const jsonStr = line.slice(6).trim();
        if (!jsonStr) continue;
        try {
          const evt = JSON.parse(jsonStr) as { event?: string; data?: Record<string, unknown> };
          const eventType = evt.event ?? evt.data?.type;
          const data = evt.data ?? {};

          if (eventType === "message.delta") {
            finalText += String(data.content ?? "");
          } else if (eventType === "tool.started" || eventType === "tool.completed") {
            toolCount++;
          } else if (eventType === "run.completed") {
            usage = (data as Record<string, unknown>).usage as Record<string, unknown> ?? null;
            finalText = String((data as Record<string, unknown>).response ?? finalText);
          } else if (eventType === "run.failed") {
            runFailed = true;
            errorMsg = String((data as Record<string, unknown>).error ?? "Unknown error");
          }
        } catch {
          // skip malformed JSON
        }
      }
    }
  } catch (e) {
    if (controller.signal.aborted) {
      return {
        exitCode: 1,
        signal: "SIGTERM",
        timedOut: true,
        summary: "Run timed out",
        resultJson: { summary: "Run timed out" },
      };
    }
    throw e;
  }

  if (runFailed) {
    return {
      exitCode: 1,
      signal: null,
      timedOut: false,
      summary: errorMsg,
      resultJson: { summary: errorMsg },
    };
  }

  const summary = finalText.slice(0, 2000) || "Run completed";
  return {
    exitCode: 0,
    signal: null,
    timedOut: false,
    summary,
    resultJson: { summary, toolCount },
    usage: usage as AdapterExecutionResult["usage"],
  };
}

export async function testEnvironment(): Promise<{ ok: boolean; error?: string }> {
  return { ok: true };
}
```

- [ ] **Step 2: Commit**

```bash
git add paperclip/server/src/adapters/hermes_remote/execute.ts
git commit -m "feat(hermes-remote): add execute function with k8s provisioning and SSE"
```

---

## Task 5: Adapter Registration

**Files:**
- Create: `paperclip/server/src/adapters/hermes_remote/index.ts`
- Modify: `paperclip/server/src/adapters/registry.ts`
- Modify: `paperclip/server/src/adapters/builtin-adapter-types.ts`

- [ ] **Step 1: Create index.ts**

```typescript
// paperclip/server/src/adapters/hermes_remote/index.ts
import type { ServerAdapterModule } from "../types.js";
import { execute, testEnvironment } from "./execute.js";

export const hermesRemoteAdapter: ServerAdapterModule = {
  type: "hermes_remote",
  execute,
  testEnvironment,
  supportsLocalAgentJwt: true,
  models: [],
  agentConfigurationDoc: `# hermes_remote agent configuration

Adapter: hermes_remote — runs agent on a remote Kubernetes cluster.

Core fields:
- k8sApiUrl (string, required): k8s API server URL (e.g. "https://k8s.example.com:6443")
- k8sNamespace (string, required): k8s namespace for agent pods (e.g. "agents")
- k8sToken (string, required): ServiceAccount bearer token for k8s API
- k8sCAData (string, optional): Base64-encoded CA certificate
- agentImage (string, required): Docker image for agent pod (e.g. "hermes-agent-remote:latest")
- imagePullSecret (string, optional): k8s Secret name for private registry
- resources (object, optional): { cpu: "1", memory: "2Gi" }
- mcpEndpoints (object, optional): HTTPS URLs for MCP servers
- providerKeys (object, optional): LLM provider API keys
- timeoutSec (number, optional): run timeout, default 3600
`,
};
```

- [ ] **Step 2: Modify `builtin-adapter-types.ts` — add `"hermes_remote"`**

Add `"hermes_remote"` to the `BUILTIN_ADAPTER_TYPES` set, after `"hermes_local"`.

- [ ] **Step 3: Modify `registry.ts` — import and register**

Add import at the top (after the http adapter import):
```typescript
import { hermesRemoteAdapter } from "./hermes_remote/index.js";
```

Add `hermesRemoteAdapter` to the adapter array in `registerBuiltInAdapters()` (after `httpAdapter`):
```typescript
    httpAdapter,
    hermesRemoteAdapter,
```

- [ ] **Step 4: Commit**

```bash
git add paperclip/server/src/adapters/hermes_remote/index.ts paperclip/server/src/adapters/registry.ts paperclip/server/src/adapters/builtin-adapter-types.ts
git commit -m "feat(hermes-remote): register adapter in Paperclip server"
```

---

## Task 6: Agent Pod Image

**Files:**
- Create: `hermes-agent-image/Dockerfile`
- Create: `hermes-agent-image/entrypoint.py`

- [ ] **Step 1: Create Dockerfile**

```dockerfile
FROM debian:13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv build-essential git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/hermes-agent
COPY hermes-agent/ .
RUN pip install --no-cache-dir --break-system-packages ".[all]"

COPY hermes-agent-image/entrypoint.py /opt/entrypoint.py

EXPOSE 8642
ENTRYPOINT ["python3", "/opt/entrypoint.py"]
```

- [ ] **Step 2: Create entrypoint.py**

```python
#!/usr/bin/env python3
import os
import sys
import re
from pathlib import Path

CONFIG_DIR = Path("/etc/hermes")
CONFIG_FILE = CONFIG_DIR / "config.yaml"
ENV_FILE = CONFIG_DIR / ".env"
SOUL_FILE = CONFIG_DIR / "SOUL.md"
PROCESSED_CONFIG = Path("/tmp/config.yaml")
PROFILE_DIR = Path("/tmp/hermes-profile")


def load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        os.environ[key] = value


def substitute_env(text: str) -> str:
    def replacer(m):
        var = m.group(1)
        return os.environ.get(var, m.group(0))
    return re.sub(r"\$\{(\w+)\}", replacer, text)


def main():
    load_env(ENV_FILE)

    if CONFIG_FILE.exists():
        raw = CONFIG_FILE.read_text()
        processed = substitute_env(raw)
        PROCESSED_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        PROCESSED_CONFIG.write_text(processed)
    else:
        print(f"WARNING: {CONFIG_FILE} not found", file=sys.stderr)
        PROCESSED_CONFIG.write_text("model:\n  default: glm-5.1\n")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if SOUL_FILE.exists():
        (PROFILE_DIR / "SOUL.md").symlink_to(SOUL_FILE)

    port = int(os.environ.get("API_SERVER_PORT", "8642"))
    api_key = os.environ.get("HERMES_API_SERVER_KEY", "")

    from gateway.platforms.api_server import ApiServerPlatform
    from gateway.config import PlatformConfig

    config = PlatformConfig(
        name="api_server",
        extra={
            "host": "0.0.0.0",
            "port": port,
            "key": api_key,
        },
    )

    platform = ApiServerPlatform(config)
    print(f"Starting hermes agent api_server on 0.0.0.0:{port}", flush=True)

    import asyncio
    asyncio.run(platform.connect())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add hermes-agent-image/Dockerfile hermes-agent-image/entrypoint.py
git commit -m "feat(hermes-remote): add agent pod Dockerfile and entrypoint"
```

---

## Task 7: Agent Operator

**Files:**
- Create: `agent-operator/Dockerfile`
- Create: `agent-operator/config.py`
- Create: `agent-operator/k8s_resources.py`
- Create: `agent-operator/reconciler.py`
- Create: `agent-operator/main.py`

- [ ] **Step 1: Create config.py**

```python
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
NAMESPACE = os.environ.get("K8S_NAMESPACE", "agents")
DEFAULT_IMAGE = os.environ.get("AGENT_IMAGE", "hermes-agent-remote:latest")
PAPERCLIP_API_URL = os.environ.get("PAPERCLIP_API_URL", "http://paperclip-server:3100/api")
```

- [ ] **Step 2: Create k8s_resources.py**

```python
import json
from typing import Dict, Optional

def agent_resource_name(agent_id: str) -> str:
    return f"agent-{agent_id[:8]}"


def make_deployment(
    name: str,
    namespace: str,
    agent_id: str,
    image: str,
    resources: Optional[Dict] = None,
    image_pull_secret: Optional[str] = None,
) -> dict:
    res = resources or {"cpu": "1", "memory": "2Gi"}
    spec = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "hermes-agent", "agent-id": agent_id, "managed-by": "paperclip-operator"},
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"agent-id": agent_id}},
            "template": {
                "metadata": {"labels": {"app": "hermes-agent", "agent-id": agent_id, "managed-by": "paperclip-operator"}},
                "spec": {
                    "containers": [{
                        "name": "agent",
                        "image": image,
                        "ports": [{"containerPort": 8642}],
                        "envFrom": [{"secretRef": {"name": f"{name}-secrets"}}],
                        "volumeMounts": [{"name": "config", "mountPath": "/etc/hermes"}],
                        "resources": {
                            "requests": res,
                            "limits": res,
                        },
                    }],
                    "volumes": [{"name": "config", "configMap": {"name": f"{name}-config"}}],
                },
            },
        },
    }
    if image_pull_secret:
        spec["spec"]["template"]["spec"]["imagePullSecrets"] = [{"name": image_pull_secret}]
    return spec


def make_service(name: str, namespace: str, agent_id: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace, "labels": {"managed-by": "paperclip-operator"}},
        "spec": {
            "selector": {"agent-id": agent_id},
            "ports": [{"port": 8642, "targetPort": 8642}],
            "type": "ClusterIP",
        },
    }


def make_config_map(name: str, namespace: str, data: Dict[str, str]) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace, "labels": {"managed-by": "paperclip-operator"}},
        "data": data,
    }


def make_secret(name: str, namespace: str, string_data: Dict[str, str]) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": namespace, "labels": {"managed-by": "paperclip-operator"}},
        "type": "Opaque",
        "stringData": string_data,
    }
```

- [ ] **Step 3: Create reconciler.py**

```python
import hashlib
import json
import logging
import time
from pathlib import Path

import psycopg2
from kubernetes import client, config as k8s_config

from . import config as cfg
from .k8s_resources import (
    agent_resource_name,
    make_config_map,
    make_deployment,
    make_secret,
    make_service,
)

logger = logging.getLogger(__name__)


def _config_hash(data: dict) -> str:
    return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()


class Reconciler:
    def __init__(self):
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        self.k8s_apps = client.AppsV1Api()
        self.k8s_core = client.CoreV1Api()
        self._agent_hashes: dict[str, str] = {}

    def _fetch_agents(self) -> list[dict]:
        conn = psycopg2.connect(cfg.DATABASE_URL)
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT a.id, a.name, a.company_id, a.adapter_type, a.adapter_config, a.status, a.role
                FROM agents a
                JOIN company_memberships cm ON cm.principal_id = a.id::text
                WHERE cm.principal_type = 'agent'
                  AND a.adapter_type = 'hermes_remote'
                  AND a.status NOT IN ('terminated', 'paused')
            """)
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def _apply_configmap(self, name: str, data: dict[str, str]):
        ns = cfg.NAMESPACE
        body = make_config_map(f"{name}-config", ns, data)
        try:
            self.k8s_core.patch_namespaced_config_map(f"{name}-config", ns, body)
        except Exception:
            self.k8s_core.create_namespaced_config_map(ns, body)

    def _apply_secret(self, name: str, string_data: dict[str, str]):
        ns = cfg.NAMESPACE
        body = make_secret(f"{name}-secrets", ns, string_data)
        try:
            self.k8s_core.patch_namespaced_secret(f"{name}-secrets", ns, body)
        except Exception:
            self.k8s_core.create_namespaced_secret(ns, body)

    def _apply_deployment(self, name: str, agent_id: str, image: str, adapter_config: dict):
        ns = cfg.NAMESPACE
        resources = adapter_config.get("resources")
        pull_secret = adapter_config.get("imagePullSecret")
        body = make_deployment(name, ns, agent_id, image, resources, pull_secret)
        try:
            self.k8s_apps.patch_namespaced_deployment(name, ns, body)
        except Exception:
            self.k8s_apps.create_namespaced_deployment(ns, body)

    def _apply_service(self, name: str, agent_id: str):
        ns = cfg.NAMESPACE
        body = make_service(name, ns, agent_id)
        try:
            self.k8s_core.patch_namespaced_service(name, ns, body)
        except Exception:
            self.k8s_core.create_namespaced_service(ns, body)

    def _delete_all(self, name: str):
        ns = cfg.NAMESPACE
        for fn in [
            lambda: self.k8s_apps.delete_namespaced_deployment(name, ns),
            lambda: self.k8s_core.delete_namespaced_service(name, ns),
            lambda: self.k8s_core.delete_namespaced_config_map(f"{name}-config", ns),
            lambda: self.k8s_core.delete_namespaced_secret(f"{name}-secrets", ns),
        ]:
            try:
                fn()
            except Exception:
                pass

    def _get_active_names(self) -> set[str]:
        try:
            deps = self.k8s_apps.list_namespaced_deployment(
                cfg.NAMESPACE, label_selector="managed-by=paperclip-operator"
            )
            return {d.metadata.name for d in deps.items}
        except Exception:
            return set()

    def reconcile(self):
        agents = self._fetch_agents()
        desired_names: set[str] = set()
        agent_ids: set[str] = set()

        for agent in agents:
            agent_id = agent["id"]
            agent_ids.add(agent_id)
            name = agent_resource_name(agent_id)
            desired_names.add(name)

            adapter_config = agent.get("adapter_config") or {}
            image = adapter_config.get("agentImage", cfg.DEFAULT_IMAGE)
            current_hash = _config_hash(adapter_config)

            if self._agent_hashes.get(agent_id) == current_hash:
                continue

            logger.info("Provisioning agent %s (%s)", agent.get("name"), agent_id[:8])

            config_data = {
                "config.yaml": "# managed by operator - populated by adapter on execute",
                "SOUL.md": f"# {agent.get('name', 'Agent')}",
            }
            self._apply_configmap(name, config_data)
            self._apply_secret(name, {"PLACEHOLDER": "true"})
            self._apply_deployment(name, agent_id, image, adapter_config)
            self._apply_service(name, agent_id)

            self._agent_hashes[agent_id] = current_hash

        active = self._get_active_names()
        for stale_name in active - desired_names:
            logger.info("Deprovisioning stale deployment %s", stale_name)
            self._delete_all(stale_name)

    def run_loop(self):
        logger.info("Starting reconciler loop (interval=%ds)", cfg.POLL_INTERVAL)
        while True:
            try:
                self.reconcile()
            except Exception as e:
                logger.error("Reconcile failed: %s", e)
            time.sleep(cfg.POLL_INTERVAL)
```

- [ ] **Step 4: Create main.py**

```python
import logging
import threading

from fastapi import FastAPI

from . import config as cfg
from .reconciler import Reconciler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Operator", version="0.1.0")

reconciler: Reconciler | None = None


@app.on_event("startup")
def startup():
    global reconciler
    reconciler = Reconciler()
    t = threading.Thread(target=reconciler.run_loop, daemon=True)
    t.start()
    logger.info("Operator started")


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    if reconciler:
        return {"managed_agents": len(reconciler._agent_hashes)}
    return {"managed_agents": 0}
```

- [ ] **Step 5: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY agent-operator/ .
RUN pip install --no-cache-dir fastapi uvicorn psycopg2-binary kubernetes

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
```

- [ ] **Step 6: Commit**

```bash
git add agent-operator/
git commit -m "feat(hermes-remote): add k8s agent operator"
```

---

## Task 8: K8s Manifests

**Files:**
- Create: `k8s/namespace.yaml`
- Create: `k8s/rbac.yaml`
- Create: `k8s/agent-operator.yaml`
- Create: `k8s/networkpolicy.yaml`

- [ ] **Step 1: Create k8s/namespace.yaml**

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: agents
  labels:
    name: agents
```

- [ ] **Step 2: Create k8s/rbac.yaml**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: agent-operator
  namespace: agents
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: agent-operator
  namespace: agents
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["services", "configmaps", "secrets"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: agent-operator
  namespace: agents
subjects:
  - kind: ServiceAccount
    name: agent-operator
    namespace: agents
roleRef:
  kind: Role
  name: agent-operator
  apiGroup: rbac.authorization.k8s.io
```

- [ ] **Step 3: Create k8s/agent-operator.yaml**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-operator
  namespace: agents
spec:
  replicas: 1
  selector:
    matchLabels:
      app: agent-operator
  template:
    metadata:
      labels:
        app: agent-operator
    spec:
      serviceAccountName: agent-operator
      containers:
        - name: operator
          image: agent-operator:latest
          ports:
            - containerPort: 8080
          env:
            - name: DATABASE_URL
              value: "postgres://paperclip:paperclip@<DB_HOST>:5432/paperclip"
            - name: K8S_NAMESPACE
              value: "agents"
            - name: AGENT_IMAGE
              value: "hermes-agent-remote:latest"
            - name: PAPERCLIP_API_URL
              value: "https://paperclip.example.com/api"
---
apiVersion: v1
kind: Service
metadata:
  name: agent-operator
  namespace: agents
spec:
  selector:
    app: agent-operator
  ports:
    - port: 8080
      targetPort: 8080
```

- [ ] **Step 4: Create k8s/networkpolicy.yaml**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agent-egress
  namespace: agents
spec:
  podSelector:
    matchLabels:
      app: hermes-agent
  policyTypes:
    - Egress
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
      ports:
        - port: 443
          protocol: TCP
        - port: 80
          protocol: TCP
```

- [ ] **Step 5: Commit**

```bash
git add k8s/
git commit -m "feat(hermes-remote): add k8s manifests for operator and agents"
```

---

## Task 9: MCP HTTPS Endpoints (Traefik Labels)

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add Traefik labels to paperclip-mcp service**

Add Traefik labels to the `paperclip-mcp` service in `docker-compose.yml`. The service needs to be on the `traefik-public` network and have labels for HTTPS routing:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.paperclip-mcp.rule=Host(`mcp.paperclip.example.com`)"
      - "traefik.http.routers.paperclip-mcp.entrypoints=websecure"
      - "traefik.http.routers.paperclip-mcp.tls=true"
      - "traefik.http.services.paperclip-mcp.loadbalancer.server.port=8082"
```

Add `traefik-public` to the networks section of `paperclip-mcp`.

- [ ] **Step 2: Add Traefik labels to rag-mcp service**

Similarly for `rag-mcp`:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.rag-mcp.rule=Host(`rag-mcp.paperclip.example.com`)"
      - "traefik.http.routers.rag-mcp.entrypoints=websecure"
      - "traefik.http.routers.rag-mcp.tls=true"
      - "traefik.http.services.rag-mcp.loadbalancer.server.port=8081"
```

Add `traefik-public` to the networks section of `rag-mcp`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(hermes-remote): add Traefik HTTPS labels for MCP endpoints"
```

---

## Task 10: Integration — Final Wiring

**Files:**
- Modify: `AGENTS.md` — add documentation for `hermes_remote` adapter

- [ ] **Step 1: Add hermes_remote section to AGENTS.md**

Add to the Architecture section of AGENTS.md, after the existing hermes-gateway documentation:

```markdown
### Hermes Remote (K8s)

Agents with `adapter_type=hermes_remote` run on remote Kubernetes clusters as individual Pods.

- **Adapter**: `paperclip/server/src/adapters/hermes_remote/` — creates k8s Deployment, Service, ConfigMap, Secret via k8s API
- **Agent image**: `hermes-agent-image/` — lightweight container with hermes-agent + api_server.py
- **Operator**: `agent-operator/` — Python FastAPI service that polls Paperclip DB and reconciles k8s resources
- **MCP over HTTPS**: Traefik exposes paperclip-mcp, rag-mcp, memory-mcp via public HTTPS URLs
- **Auth**: Permanent `pcp_*` API keys (same as hermes_local)
- **Config**: `adapter_config` stores k8s connection details, MCP endpoints, provider keys
- **K8s manifests**: `k8s/` — namespace, RBAC, operator deployment, network policy

**Migration**: Change agent's `adapter_type` from `hermes_local` to `hermes_remote` and set `adapter_config` with k8s connection details. No downtime for existing agents.

**Build**: `docker build -t hermes-agent-remote:latest -f hermes-agent-image/Dockerfile .` and `docker build -t agent-operator:latest agent-operator/`
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: add hermes_remote k8s adapter documentation"
```
