# Hermes Remote K8s Adapter — Design Spec

## Summary

Enable Paperclip agents to run on remote Kubernetes clusters as individual Pods, using the HTTP adapter pattern from `docs/adapters/http.md`. Paperclip creates/manages agent Pods via k8s API and communicates over HTTPS.

## Motivation

Currently all agents run as supervisor processes inside a single `hermes-gateway` container on the main host. This creates:
- Resource contention (all agents share CPU/memory of one host)
- Single point of failure
- No horizontal scalability
- Tight coupling to Docker Compose

Remote K8s deployment allows agents to run on separate infrastructure with independent scaling.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  Main Host (Docker Compose)                  │
│                                                             │
│  Paperclip-server                                           │
│  ├── hermes_local adapter (existing, local agents)          │
│  └── hermes_remote adapter (NEW, remote agents)             │
│       ├── k8s provisioner (create/update/delete Deployments)│
│       └── SSE executor (POST /v1/runs → stream events)      │
│                                                             │
│  Traefik (expose MCP endpoints via HTTPS)                   │
│  ├── mcp.paperclip.example.com → paperclip-mcp:8082         │
│  ├── rag.example.com/mcp → rag-mcp:8081                     │
│  └── memory.paperclip.example.com → memory_mcp:8680         │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTPS
          ┌────────────┴────────────────────┐
          │       Kubernetes Cluster         │
          │                                 │
          │  ┌────────────────────────────┐ │
          │  │ agent-operator             │ │
          │  │ (Deployment, 1 replica)    │ │
          │  │ Poll DB → CRUD Deployments │ │
          │  └────────────────────────────┘ │
          │                                 │
          │  ┌──────────┐  ┌──────────┐    │
          │  │ agent-A  │  │ agent-B  │    │
          │  │ (Pod)    │  │ (Pod)    │    │
          │  │ :8642    │  │ :8642    │    │
          │  │ api_     │  │ api_     │    │
          │  │ server   │  │ server   │    │
          │  └──────────┘  └──────────┘    │
          └────────────────────────────────┘
```

## Components

### 1. `hermes_remote` Adapter (TypeScript)

**Location:** `paperclip/server/src/adapters/hermes_remote/`

**Files:**
- `index.ts` — adapter registration (ServerAdapterModule)
- `execute.ts` — main execute function
- `k8s-client.ts` — k8s API wrapper (create/patch/delete Deployment, Service, ConfigMap, Secret)
- `config-builder.ts` — generate agent config.yaml and SOUL.md for ConfigMap

**Registration:**
```typescript
// paperclip/server/src/adapters/registry.ts
const hermesRemoteAdapter: ServerAdapterModule = {
  type: "hermes_remote",
  execute: hermesRemoteExecute,
  testEnvironment: hermesRemoteTestEnv,
  supportsLocalAgentJwt: true,
  models: [],
  agentConfigurationDoc: "...",
};
```

Add `"hermes_remote"` to `BUILTIN_ADAPTER_TYPES` in `builtin-adapter-types.ts`.

**Adapter Config Schema (stored in `agents.adapter_config`):**
```typescript
interface HermesRemoteConfig {
  // K8s cluster access
  k8sApiUrl: string;          // e.g. "https://k8s.example.com:6443"
  k8sNamespace: string;       // e.g. "agents"
  k8sToken: string;           // ServiceAccount token (stored as secret)
  k8sCAData?: string;         // Base64 CA cert for TLS verification

  // Agent image
  agentImage: string;         // e.g. "hermes-agent-remote:latest"
  imagePullSecret?: string;   // For private registries

  // Resources per agent Pod
  resources?: {
    cpu: string;              // e.g. "1"
    memory: string;           // e.g. "2Gi"
  };

  // MCP endpoints (public HTTPS)
  mcpEndpoints?: {
    paperclip?: string;       // e.g. "https://mcp.paperclip.example.com"
    rag?: string;             // e.g. "https://rag.example.com/mcp"
    memory?: string;          // e.g. "https://memory.paperclip.example.com"
    outline?: string;         // e.g. "https://outline.example.com/mcp"
  };

  // Timeout
  timeoutSec?: number;        // default 3600
}
```

**Execute Flow:**
1. Parse `adapterConfig` as `HermesRemoteConfig`
2. Read agent instructions via Paperclip API (or local volume if accessible)
3. Generate k8s resource manifests:
   - `ConfigMap` with `config.yaml`, `SOUL.md`, `.env`
   - `Secret` with provider API keys, Paperclip API key
   - `Deployment` with single container from `agentImage`
   - `Service` (ClusterIP) pointing to Pod port 8642
4. Apply resources via k8s API (create if not exists, patch if changed)
5. Wait for Pod readiness (with timeout)
6. POST to `http://<pod-service>.<namespace>:8642/v1/runs` with body:
   ```json
   {
     "input": "<task prompt from buildInputMessage()>",
     "instructions": "<system prompt>",
     "paperclip_api_key": "<pcp_* key>",
     "heartbeat_run_id": "<run UUID>"
   }
   ```
7. Read SSE stream from `GET /v1/runs/{run_id}/events`
8. Parse events, accumulate usage, build resultJson
9. Return `AdapterExecutionResult`

**SSE Event Handling** (reuses pattern from hermes-paperclip-adapter):
- `message.delta` → accumulate text
- `tool.started` / `tool.completed` → log tools used
- `run.completed` → extract final response, usage
- `run.failed` → extract error, return failed result

**K8s Resource Naming Convention:**
- Deployment: `agent-<agent-id-short>` (first 8 chars of UUID)
- Service: `agent-<agent-id-short>`
- ConfigMap: `agent-<agent-id-short>-config`
- Secret: `agent-<agent-id-short>-secrets`
- Labels: `app=hermes-agent`, `agent-id=<uuid>`, `managed-by=paperclip`

**ConfigMap Contents:**

`config.yaml`:
```yaml
agent:
  model: glm-5.1
  reasoning_effort: none
  tool_use_enforcement: auto
  compression:
    threshold: 0.85

mcp_servers:
  paperclip:
    url: ${MCP_PAPERCLIP_URL}
    transport: streamable-http
  rag:
    url: ${MCP_RAG_URL}
    transport: streamable-http
  memory:
    url: ${MCP_MEMORY_URL}
    transport: streamable-http
  outline:
    url: ${MCP_OUTLINE_URL}
    transport: streamable-http
    headers:
      Authorization: Bearer ${OUTLINE_API_KEY}
```

`SOUL.md`: fetched from Paperclip instructions API or generated via `_build_soul_md()` logic.

`.env`:
```
PAPERCLIP_API_URL=https://paperclip.example.com/api
PAPERCLIP_RUN_API_KEY=pcp_...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
# ... other provider keys from adapter config
```

### 2. Agent Pod Image

**Location:** `hermes-agent-image/`

**Dockerfile:**
```dockerfile
FROM debian:13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv build-essential git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/hermes-agent
COPY hermes-agent/ .
RUN pip install --no-cache-dir ".[all]"

COPY hermes-agent-image/entrypoint.py /opt/entrypoint.py

EXPOSE 8642
ENTRYPOINT ["python3", "/opt/entrypoint.py"]
```

**`entrypoint.py`:**
- Reads `.env` from `/etc/hermes/.env` and sets env vars (dotenv)
- Reads config from `/etc/hermes/config.yaml` (mounted from ConfigMap)
- Performs `${VAR}` substitution in config.yaml using current env vars
- Reads SOUL.md from `/etc/hermes/SOUL.md`
- Writes processed config to `/tmp/config.yaml`
- Starts `api_server.py` on port 8642 with processed config

**Alternative: re-use hermes-agent/gateway directly:**
```bash
python3 -m gateway.platforms.api_server --port 8642 --config /etc/hermes/config.yaml
```

The api_server.py already supports standalone mode with config file. The Pod runs a single agent process.

### 3. Agent Operator

**Location:** `agent-operator/`

Python FastAPI service deployed in the k8s cluster.

**Responsibilities:**
1. Poll Paperclip PostgreSQL every 60 seconds (same pattern as hermes-gateway orchestrator)
2. Query agents with `adapter_type='hermes_remote'` and status not terminated/paused
3. For each agent:
   - Create Deployment + Service + ConfigMap + Secret if not exists
   - Update ConfigMap when instructions/adapter_config changes (detect via hash)
   - Delete Deployment when agent is deactivated/deleted
4. Health check endpoint `/healthz`
5. Metrics endpoint `/metrics` (agent count, provisioning status)

**Why both adapter AND operator manage k8s resources?**
- **Adapter**: Creates Pod on first heartbeat (ensures Pod exists before execute). Handles the "just-in-time" case.
- **Operator**: Keeps Pods in sync with DB changes. Handles config updates, cleanup of deleted agents, and long-term lifecycle management. Runs independently of heartbeats.

The adapter uses `CreateOrReplace` semantics (k8s `apply`), so both can safely create/update without conflict.

**Operator Config:**
```yaml
# agent-operator/config.yaml
database_url: "postgres://paperclip:paperclip@<host>:5432/paperclip"
poll_interval: 60
namespace: "agents"
default_image: "hermes-agent-remote:latest"
paperclip_api_url: "https://paperclip.example.com/api"
```

**Operator Dockerfile:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY agent-operator/ .
RUN pip install --no-cache-dir fastapi uvicorn psycopg2-binary kubernetes
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### 4. MCP HTTPS Endpoints

Add Traefik labels to existing MCP services in `docker-compose.yml`:

```yaml
paperclip-mcp:
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.paperclip-mcp.rule=Host(`mcp.paperclip.example.com`)"
    - "traefik.http.routers.paperclip-mcp.tls=true"
    - "traefik.http.services.paperclip-mcp.loadbalancer.server.port=8082"

rag-mcp:
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.rag-mcp.rule=Host(`rag-mcp.paperclip.example.com`)"
    - "traefik.http.routers.rag-mcp.tls=true"
    - "traefik.http.services.rag-mcp.loadbalancer.server.port=8081"
```

For memory MCP (runs inside hermes-gateway):
- Either expose port 8680 via Traefik
- Or run memory_mcp_server as a separate service

Auth: `pcp_*` API key in MCP request headers (already supported).

## Data Flows

### Heartbeat Run (Remote Agent)

```
1. Paperclip heartbeat cron creates heartbeat_run in DB
2. executeRun() → getServerAdapter("hermes_remote")
3. hermes_remote.execute():
   a. Parse HermesRemoteConfig from adapter_config
   b. Ensure k8s Deployment exists (create if needed)
   c. Wait for Pod readiness
   d. POST http://agent-<id>.agents:8642/v1/runs
      Body: { input, instructions, paperclip_api_key, heartbeat_run_id }
   e. Read SSE stream → parse events
   f. Return AdapterExecutionResult
4. Paperclip writes result to heartbeat_runs
```

### Agent MCP Tool Calls

```
Agent in k8s Pod
  → MCP paperclip (HTTPS) → Traefik → paperclip-mcp → Paperclip API
  → MCP rag (HTTPS)       → Traefik → rag-mcp → Qdrant
  → MCP memory (HTTPS)    → Traefik → memory_mcp → Qdrant
  → MCP outline (HTTPS)   → Outline API (external)
```

### Operator Lifecycle

```
Operator polls DB every 60s:
  → SELECT agents with adapter_type='hermes_remote'
  → For new agents: create Deployment, Service, ConfigMap, Secret
  → For changed agents: patch ConfigMap (triggers Pod restart)
  → For removed agents: delete Deployment (cascade)
```

## Error Handling

| Scenario | Handling |
|----------|----------|
| Pod not ready after timeout (120s) | Return failed run with error "Agent pod not ready" |
| k8s API unreachable | Return failed run with error "K8s API unavailable" |
| SSE stream breaks mid-run | Return partial result with warning |
| Agent returns text-only (no tools) | Promise detection + continuation (same as hermes_local) |
| Pod crashes during run | Detect via SSE disconnect, return failed run |
| ConfigMap update fails | Log error, retry on next heartbeat |
| Multiple heartbeats for same agent | k8s Deployment ensures single Pod, api_server handles concurrency |

## Security

- **k8s API auth**: ServiceAccount token with RBAC (only manage resources in target namespace)
- **Agent auth**: Permanent `pcp_*` API keys (same as hermes_local)
- **MCP auth**: `pcp_*` key in headers over HTTPS
- **Secret management**: k8s Secrets for API keys, not in ConfigMaps
- **Network**: k8s NetworkPolicy to restrict Pod egress (only to Paperclip, LLM providers, MCP endpoints)
- **No Docker socket**: Remote agents do NOT mount docker socket

## Migration Path

1. Add `hermes_remote` adapter type (no changes to existing agents)
2. Deploy agent-operator to k8s cluster
3. Build and push agent image to registry
4. Configure MCP HTTPS endpoints via Traefik
5. For each agent to migrate: change `adapter_type` from `hermes_local` to `hermes_remote` and set `adapter_config`
6. Verify heartbeat runs succeed
7. Remove agent from local hermes-gateway (deprovision)

No downtime for existing agents during migration.

## File Structure

```
hw-rnd-ai-crew/
├── paperclip/server/src/adapters/hermes_remote/
│   ├── index.ts              # Adapter registration
│   ├── execute.ts            # Execute function (k8s provision + SSE)
│   ├── k8s-client.ts         # K8s API wrapper
│   ├── config-builder.ts     # Generate config.yaml, SOUL.md
│   └── types.ts              # HermesRemoteConfig types
├── hermes-agent-image/
│   ├── Dockerfile            # Agent Pod image
│   └── entrypoint.py         # Startup script
├── agent-operator/
│   ├── Dockerfile            # Operator image
│   ├── main.py               # FastAPI app
│   ├── reconciler.py         # Reconcile loop (poll DB → CRUD k8s)
│   ├── k8s_resources.py      # Generate k8s manifests
│   └── config.py             # Operator configuration
├── k8s/
│   ├── agent-operator.yaml   # Operator Deployment + RBAC
│   ├── namespace.yaml        # Target namespace for agents
│   └── networkpolicy.yaml    # Default NetworkPolicy for agent Pods
└── docker-compose.yml         # Add Traefik labels for MCP HTTPS
```

## Dependencies

- `@kubernetes/client-node` (or raw HTTPS to k8s API) — in paperclip-server
- `kubernetes` Python package — in agent-operator
- Existing `api_server.py` from hermes-agent — in agent Pod
- Existing `hermes-paperclip-adapter` SSE parsing logic — reusable in adapter

## Out of Scope

- Agent auto-scaling (HPA based on queue depth)
- Multi-cluster deployment
- Agent federation across clusters
- GPU resource management
- Agent-to-agent communication
