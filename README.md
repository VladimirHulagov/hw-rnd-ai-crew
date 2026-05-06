# HW RND AI Crew

Infrastructure for the AI crew: RAG pipeline over Nextcloud files, Paperclip server, and Hermes agent integration.

## Architecture

### Service Topology

```
                                 ┌──────────────────────┐
                                 │       INTERNET        │
                                 │  Users / Telegram /   │
                                 │  LLM Providers (API)  │
                                 └──────────┬───────────┘
                                            │
                              ┌─────────────┴──────────────┐
                              │        Traefik             │
                              │    (TLS termination,       │
                              │     reverse proxy)         │
                              │    network: traefik-public │
                              └──┬──────────┬──────────┬───┘
                    paperclip.*  │          │          │  rag.*
                                 │          │          │
           ┌──────────────────────┘          │          └───────────────────────┐
           ▼                                 ▼                                  ▼
┌─────────────────────┐          ┌──────────────────┐              ┌───────────────────┐
│  paperclip-server   │          │     rag-mcp      │              │   paperclip-mcp   │
│     (:3100)         │          │     (:8081)      │              │     (:8082)       │
│                     │          │                  │              │                   │
│ ┌─────────────────┐ │          │ MCP tools:       │              │ MCP tools:        │
│ │ REST API        │ │          │ • search_nextcloud│              │ • paperclip_list_* │
│ │ (auth, CRUD,    │ │          │ • search_outline │              │ • paperclip_set_*  │
│ │  heartbeat,     │ │          │ • list_outline_* │              │ • paperclip_update*│
│ │  budgets,       │ │          │                  │              │   (23 tools)      │
│ │  skills)        │ │          └────────┬─────────┘              └────────┬──────────┘
│ └─────────────────┘ │                   │                                 │
│ ┌─────────────────┐ │                   ▼                                 │
│ │ UI (Vite SPA)   │ │          ┌──────────────────┐                       │
│ │ (React)         │ │          │      Qdrant      │                       │
│ └─────────────────┘ │          │   (:6333/6334)   │                       │
│ ┌─────────────────┐ │          └──────────────────┘                       │
│ │ Heartbeat Svc   │ │                                                     │
│ │ (cron → runs)   │ │                                                     │
│ └────────┬────────┘ │                                                     │
└──────────┼──────────┘                                                     │
           │                                                                │
           │  POST /v1/runs (SSE)     ┌────────────────────────┐           │
           │  (heartbeat_run_id +     │    hermes-gateway      │           │
           │   pcp_* API key)         │   (Supervisor PID 1)   │           │
           │                     ┌───►│                        │           │
           └─────────────────────┼───►│ orchestrator.py        │           │
                                 │    │ gateway × N (api_server)│           │
                                 │    │ session_indexer.py      │           │
                                 │    │ memory_mcp_server       │           │
                                 │    │ rag-worker              │           │
                                 │    └────────────────────────┘           │
           │                                                            │
           ▼  ▼                                                         │
┌──────────────────┐    ┌──────────────┐    ┌──────────────────┐        │
│  paperclip-db    │    │    Ollama     │    │  docker-guard    │        │
│  PostgreSQL 17   │    │   (:11434)   │    │    (:2375)       │        │
│  (:5432)         │    └──────────────┘    └──────────────────┘        │
└──────────────────┘                                                    │
                                                                        │
┌─── External integrations ─────────────────────────────────────────┐   │
│  Telegram (per-agent bots) · GitHub (skill git sync)              │   │
│  LLM Providers: GLM, ZAI, Gemini, OpenRouter                      │   │
└───────────────────────────────────────────────────────────────────┘   │
                                                                        │
┌─── Shared Docker Volumes ─────────────────────────────────────────┐   │
│  paperclip_pgdata · paperclip_data · hermes_profiles               │   │
│  hermes_venv · hermes_src · gateway_ports · qdrant_data · ollama_data│   │
└────────────────────────────────────────────────────────────────────┘   │
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
         ├────► paperclip-mcp ──► paperclip-server API
         ├────► rag-mcp ──► Qdrant (semantic search)
         ├────► Outline MCP ──► Outline API
         ├────► memory MCP (:8680) ──► Qdrant
         ├────► nextcloud-mcp ──► Nextcloud WebDAV
         └────► docker-guard ──► Docker daemon

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
| **hermes-gateway** | 8642-8673 | Hermes agent runtime (Supervisor PID 1, N gateway processes) |
| **paperclip-mcp** | 8082 | MCP server: 23 Paperclip tools (list/set/update issues, skills, checklist) |
| **rag-mcp** | 8081 | MCP server: search_nextcloud, search_outline, list tools |
| **rag-worker** | 8080 | FastAPI indexer: Outline (300s) + Nextcloud (600s) sync → Qdrant |
| **qdrant** | 6333 | Vector database (outline_docs, agent_memory, nextcloud_* collections) |
| **ollama** | 11434 | Local LLM (nomic-embed-text 768d, llama3, qwen) |
| **docker-guard** | 2375 | Docker API proxy (filtered GET, blocked write) |

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

## Agent Adapters

| Adapter | Description |
|---------|-------------|
| `hermes_local` | Agent runs in hermes-gateway container (supervisor process) |
| `hermes_remote` | Agent runs as k8s Pod on remote cluster (POST /v1/runs via HTTPS) |
| `http` | Fire-and-forget webhook to external service |

## Tech Stack

- **Paperclip** (TypeScript/Node.js) — agent control plane, REST API, Vite SPA
- **Hermes Agent** (Python) — AI agent with MCP client, 73+ built-in skills
- **Hermes Gateway** (Python/Supervisor) — orchestrator, N gateway processes, session indexer
- **FastAPI** (Python) — rag-worker, rag-mcp, paperclip-mcp, memory-mcp
- **Qdrant** — vector database (768d cosine, nomic-embed-text)
- **PostgreSQL 17** — Paperclip data (agents, issues, skills, heartbeat_runs)
- **Ollama** — local LLM (embeddings, optional inference)
- **Traefik** — TLS termination, reverse proxy, MCP HTTPS endpoints
- **Docker Compose** — orchestration
- **Kubernetes** (optional) — remote agent deployment via `hermes_remote` adapter
