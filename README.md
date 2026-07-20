# HW RND AI Crew

Infrastructure for the AI crew: RAG pipeline over Nextcloud files, Paperclip server, and Hermes agent integration.

## Architecture

### Service Topology

```
                              ┌──────────────────────┐
                              │       INTERNET        │
                              │  Users / Telegram /   │
                              │  LLM Providers (API)  │
                              │  Git clients (git.*)  │
                              └──────────┬───────────┘
                                         │
                           ┌─────────────┴──────────────┐
                           │         Traefik            │
                           │  (TLS termination,         │
                           │   reverse proxy)           │
                           │   network: traefik-public  │
                           └──┬────────┬────────┬───────┘
                              │        │        │
                paperclip.*   │        │ rag-mcp.* / mcp.*    git.*
                              │        │        │
                ┌─────────────┘        │        └─────────────┐
                ▼                      ▼                      ▼
     ┌─────────────────────┐ ┌──────────────────┐ ┌─────────────────────┐
     │  paperclip-server   │ │     rag-mcp      │ │      forgejo        │
     │      (:3100)        │ │     (:8081)      │ │   git.* (:3000)     │
     │                     │ │                  │ │  skill-sync repos   │
     │  REST API · UI      │ │ search_nextcloud │ │  (branch-protected) │
     │  Heartbeat Svc      │ │ search_outline   │ └──────────┬──────────┘
     │  (cron → runs)      │ │ list_outline_docs│            │ pull/push
     │  Budgets · Skills   │ └────────┬─────────┘            ▼
     └──────────┬──────────┘          │           ┌─────────────────────┐
                │                     ▼           │     forgejo-ci      │
                │           ┌──────────────────┐  │  webhook CI runner  │
                │           │      Qdrant      │  │  (skill-sync status)│
                │           │   (:6333/6334)   │  └─────────────────────┘
                │           │  • outline_docs   │
                │           │  • agent_memory   │
                │           │  • nextcloud_*    │
                │           └──────────────────┘
                │ POST /v1/runs (SSE)
                │ pcp_* key + run_id
                ▼
     ┌──────────────────────────────────────────────────────────────┐
     │             hermes-gateway (Supervisor PID 1)                │
     │  ┌────────────────────────────────────────────────────────┐  │
     │  │ orchestrator.py    60s reconcile (DB → config → super)  │  │
     │  │ gateway × N        :8642-8673 (api_server.py per agent) │  │
     │  │ session_indexer    10 min cron → Qdrant agent_memory    │  │
     │  │ memory_mcp_server  :8680 (MCP: search_memory, get_ctx)  │  │
     │  │ team_skills_api    :8681 (paperclip team-skills proxy)  │  │
     │  │ skill_sync_mcp     :8683 (MCP: skill_push ↔ Forgejo)    │  │
     │  └────────────────────────────────────────────────────────┘  │
     │  Mounts: hermes_profiles(rw) · paperclip_data(ro)            │
     │          hermes_venv · hermes_src · gateway_ports(rw)        │
     │          ./hermes-gateway/skills(ro) · agent_api_keys.json   │
     └──┬─────────────┬─────────────┬─────────────┬─────────────┬───┘
        │             │             │             │             │
        ▼             ▼             ▼             ▼             ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
   │ paperclip│  │  Ollama  │  │ docker-  │  │  rag-    │  │nextcloud │
   │   -db    │  │ (:11434) │  │  guard   │  │ worker   │  │   -mcp   │
   │PostgreSQL│  │nomic-emb │  │ (:2375)  │  │ (:8080)  │  │(internal)│
   │  17      │  │ -text    │  │+agent-   │  │Outline   │  │→Nextcloud│
   │ (:5432)  │  │llama3    │  │ deploy/  │  │Nextcloud │  │  WebDAV  │
   └────┬─────┘  └──────────┘  └────┬─────┘  └──────────┘  └────┬─────┘
        │                           │                           │
        │ reads/writes              │ filtered API              │ file ops
        │ (agents, issues,          │ (GET allowed,             │ (read/write/
        │  skills, heartbeat_runs,  │  writes blocked)          │  list/upload/
        │  budgets, activity_log)   │                           │  download)
        ▼                           ▼                           ▼
   PostgreSQL 17               /var/run/docker.sock        Nextcloud WebDAV
```

### Supporting infrastructure

```
 ┌─── outline_internal network ────────────────────┐
 │  Outline (outline.collaborationism.tech)         │
 │  ◄── hermes-gateway MCP (Bearer ol_api_...)      │
 │      create / update / search docs               │
 └──────────────────────────────────────────────────┘

 ┌─── nextcloud-rag network ────────────────────────┐
 │  Nextcloud (file storage)                        │
 │  ◄── rag-worker (WebDAV, indexing every 600s)    │
 │  ◄── nextcloud-mcp (agent file read/write)       │
 └──────────────────────────────────────────────────┘

 ┌─── External integrations ────────────────────────┐
 │  Telegram (per-agent bots)                       │
 │  LLM Providers (credential pool, rotates):       │
 │    GLM, ZAI, Gemini, OpenRouter                  │
 └──────────────────────────────────────────────────┘

 ┌─── Shared Docker Volumes ────────────────────────┐
 │  paperclip_pgdata   ← paperclip-db persistence   │
 │  paperclip_data     ← agent instructions, prompts│
 │  hermes_profiles    ← agent sessions, skills     │
 │  hermes_venv        ← pip-installed hermes-agent │
 │  hermes_src         ← hermes-agent-build copy    │
 │  hermes_instances   ← hermes instance data       │
 │  gateway_ports      ← ports.json (shared rw)     │
 │  qdrant_data        ← vector embeddings          │
 │  ollama_data        ← LLM models                 │
 │  forgejo_data       ← Forgejo git repos + LFS    │
 └──────────────────────────────────────────────────┘
```

### Heartbeat Run Flow

```
 1. paperclip-server ── heartbeat cron ──► create heartbeat_run in DB
         │
         │ 2. Adapter invocation (in-process)
         ▼
    hermes-paperclip-adapter (execute.ts)
    • buildInputMessage() → ~400 chars task prompt
    • Read ports.json → agent gateway port
    • POST /v1/runs (SSE) ─────────────────────────┐
                                                    │
         3. Agent execution                         │
         ┌──────────────────────────────────────────┘
         ▼
    hermes-gateway / api_server.py (:8642+)
    • Validate pcp_* key
    • Set HEARTBEAT_RUN_ID
    • AIAgent.run_conversation()
      → LLM API call → tool_use loop → text-only retry (×2)
         │
          │ 4. MCP tool calls (during agent loop)
          ├────► paperclip-mcp (:8082) ──► paperclip-server API
          ├────► rag-mcp (:8081) ──► Qdrant (search_outline, search_nextcloud)
          ├────► Outline MCP (external) ──► Outline API
          ├────► memory MCP (:8680) ──► Qdrant (agent_memory)
          ├────► nextcloud-mcp ──► Nextcloud WebDAV
          ├────► skill_sync MCP (:8683) ──► Forgejo (skill_push / skill_pull)
          └────► docker-guard (:2375) ──► Docker daemon (filtered GET, blocked write)

         5. Result ◄─────────────────────────────────
    adapter: Parse SSE stream → return resultJson: { summary }
         │
         ▼
    paperclip-server: write result_json → create issue comment → release lock
```

### Hermes Remote K8s

```
 Main Host (Docker Compose)                Kubernetes Cluster
 ┌──────────────────────────────┐         ┌────────────────────────────┐
 │  paperclip-server            │         │  agent-operator            │
 │  ├── hermes_local adapter    │  HTTPS  │  (poll DB → CRUD Pods)    │
 │  └── hermes_remote adapter ──┼────────►│                            │
 │       ├── k8s provisioner    │         │  ┌────────┐ ┌────────┐    │
 │       └── SSE executor       │         │  │agent-A │ │agent-B │    │
 │                              │         │  │ (Pod)  │ │ (Pod)  │    │
 │  Traefik (MCP via HTTPS)     │◄────────│  │:8642   │ │:8642   │    │
 │  ├── mcp.paperclip.*         │  MCP    │  └────────┘ └────────┘    │
 │  ├── rag-mcp.*               │  HTTPS  │                            │
 │  └── memory.*                │         │  k8s/ manifests:           │
 └──────────────────────────────┘         │  namespace · rbac · netpol │
                                           └────────────────────────────┘
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| **paperclip-server** | 3100 | Paperclip AI agent control plane (REST API, heartbeat, skills, budgets) |
| **paperclip-db** | 5432 | PostgreSQL 17 for Paperclip |
| **hermes-gateway** | 8642-8673 | Hermes agent runtime (Supervisor PID 1, N gateway processes, orchestrator) |
| **hermes-gateway** | 8680 | memory_mcp_server (search_memory, get_agent_context) |
| **hermes-gateway** | 8681 | team_skills_api (target of paperclip-server team-skills proxy) |
| **hermes-gateway** | 8683 | skill_sync_mcp (skill_push / skill_pull ↔ Forgejo) |
| **paperclip-mcp** | 8082 | MCP server: 23 Paperclip tools (list/set/update issues, skills, checklist) |
| **rag-mcp** | 8081 | MCP server: search_nextcloud, search_outline, list tools |
| **rag-worker** | 8080 | FastAPI indexer: Outline (300s) + Nextcloud (600s) sync → Qdrant |
| **nextcloud-mcp** | internal | MCP server: Nextcloud file tools (read/write/list/upload/download) |
| **qdrant** | 6333 | Vector database (outline_docs, agent_memory, nextcloud_* collections) |
| **ollama** | 11434 | Local LLM (nomic-embed-text 768d, llama3, qwen) |
| **docker-guard** | 2375 | Docker API proxy (filtered GET, blocked write, agent-deploy sandbox) |
| **forgejo** | 3000 | Self-hosted Git server (git.*) — skill-sync repos, branch-protected `main` |
| **forgejo-ci** | internal | Webhook-driven CI runner for skill-sync repos (status checks) |

## Submodules

| Submodule | Repo | Description |
|-----------|------|-------------|
| `paperclip/` | [VladimirHulagov/paperclip](https://github.com/VladimirHulagov/paperclip) | Paperclip platform (server + UI + shared packages) |
| `hermes-agent/` | [VladimirHulagov/hermes-agent](https://github.com/VladimirHulagov/hermes-agent) | Hermes AI agent (MCP client, 73+ built-in skills) |
| `hermes-paperclip-adapter/` | [VladimirHulagov/hermes-paperclip-adapter](https://github.com/VladimirHulagov/hermes-paperclip-adapter) | Hermes-Paperclip heartbeat adapter |
| `paperclip-mcp/` | [VladimirHulagov/paperclip-mcp](https://github.com/VladimirHulagov/paperclip-mcp) | Paperclip MCP server (23 tools) |
| `rag-worker/` | [VladimirHulagov/rag-worker](https://github.com/VladimirHulagov/rag-worker) | File indexer: Outline + Nextcloud → Qdrant |
| `rag-mcp/` | [VladimirHulagov/rag-mcp](https://github.com/VladimirHulagov/rag-mcp) | MCP server: semantic search tools |
| `docker-guard/` | [VladimirHulagov/docker-guard](https://github.com/VladimirHulagov/docker-guard) | Docker API proxy with filtered access |
| `superpowers/` | [VladimirHulagov/superpowers](https://github.com/VladimirHulagov/superpowers) | Agent skills and development superpowers |

## Quick Start

### 1. Clone with submodules

```bash
git clone --recurse-submodules https://github.com/VladimirHulagov/hw-rnd-ai-crew.git
cd hw-rnd-ai-crew
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your actual credentials
```

### 3. Create external networks

```bash
docker network create local-ai-internal
docker network create nextcloud-rag
docker network create outline_internal
docker network create traefik-public
```

### 4. Start services

```bash
docker compose up -d
```

### 5. Initial indexing

```bash
docker exec rag-worker python -m rag.index_all
```

## RAG Pipeline

### Indexing Sources

| Source | Interval | Target Collection | Method |
|--------|----------|-------------------|--------|
| Outline (knowledge base) | 300s | `outline_docs` | REST API |
| Nextcloud (files) | 600s | `nextcloud_*` | WebDAV |
| Agent sessions + MEMORY.md | 10min | `agent_memory` | File scan |

All embeddings use **Ollama nomic-embed-text** (768d, cosine similarity).

### MCP Tools

**rag-mcp** (`https://rag.example.com/mcp`):

| Tool | Description |
|------|-------------|
| `search_nextcloud` | Semantic search over Nextcloud files |
| `search_outline` | Semantic search over Outline documents |
| `list_outline_documents` | List indexed Outline documents |

**paperclip-mcp** (`https://mcp.paperclip.example.com/mcp`):

| Tool | Description |
|------|-------------|
| `paperclip_list_issues` | List assigned issues |
| `paperclip_update_issue` | Update issue status, description |
| `paperclip_set_checklist` | Set task checklist (native, persistent in DB) |
| `paperclip_checkout_issue` | Checkout issue for work |
| `paperclip_release_issue` | Release issue lock |
| + 18 more tools | Comments, skills, budgets, etc. |

**memory-mcp** (internal `:8680`):

| Tool | Description |
|------|-------------|
| `search_memory` | Semantic search over agent session history |
| `get_agent_context` | Get agent context by name |

**skill_sync-mcp** (internal `:8683`, inside hermes-gateway):

| Tool | Description |
|------|-------------|
| `skill_push` | Push agent-created skill to Forgejo (per-agent branch + PR) |
| `skill_pull` | Pull latest skill content from Forgejo |
| `skill_list_remote` | List skills in Forgejo repo |

## Skill Sync Pipeline

Bidirectional sync between agent profiles and **Forgejo** (`git.<domain>`) enables skills created by agents to be persisted, reviewed via PRs, and shared.

```
  Agent (skill_manage tool)
        │
        ▼
  ~/.hermes/profiles/<agentId>/skills/<category>/<slug>/SKILL.md
        │
        ▼  (orchestrator, every 60s)
  skill_scanner.py ──► company_skills DB (sourceKind: agent_created)
        │
        ▼  (orchestrator, every 10 min, fallback)
  skill_git_sync.py ──► Forgejo branch skills-sync/<md5(source_id)[:12]>
        │                  ├── 3-way merge with origin/main
        │                  └── PR via Forgejo API (structured body)
        ▼
  Forgejo repo (one per company, branch-protected main)
        │
        ▼  (orchestrator, every 60s)
  skill_git_sync.py pull ──► company_skills DB (sourceKind: git_sync)
```

Agent-facing path: `skill_push` MCP tool (immediate, via skill_sync MCP on `:8683`). Orchestrator pull: every 60s. Orchestrator push: every 10 min (env `SKILL_SYNC_PUSH_INTERVAL`, default 600s).

## Agent Adapters

| Adapter | Description |
|---------|-------------|
| `hermes_local` | Agent runs in hermes-gateway container (supervisor process) |
| `hermes_remote` | Agent runs as k8s Pod on remote cluster (POST /v1/runs via HTTPS) |
| `http` | Fire-and-forget webhook to external service |

## Tech Stack

- **Paperclip** (TypeScript/Node.js) — agent control plane, REST API, Vite SPA
- **Hermes Agent** (Python) — AI agent with MCP client, 73+ built-in skills
- **Hermes Gateway** (Python/Supervisor) — orchestrator, N gateway processes, session indexer, skill sync
- **FastAPI** (Python) — rag-worker, rag-mcp, paperclip-mcp, nextcloud-mcp, memory-mcp, skill_sync-mcp
- **Qdrant** — vector database (768d cosine, nomic-embed-text)
- **PostgreSQL 17** — Paperclip data (agents, issues, skills, heartbeat_runs)
- **Ollama** — local LLM (embeddings, optional inference)
- **Forgejo** — self-hosted Git server for skill-sync repos
- **Traefik** — TLS termination, reverse proxy, MCP HTTPS endpoints
- **Docker Compose** — orchestration
- **Kubernetes** (optional) — remote agent deployment via `hermes_remote` adapter
