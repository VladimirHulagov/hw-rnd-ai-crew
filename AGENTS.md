# Agents

## Project Overview

HW RND AI Crew is a Docker Compose stack providing RAG over Nextcloud files, Paperclip (AI agent control plane), and Hermes agent integration. Traefik handles TLS/routing. Services run on an internal network behind `paperclip.example.com` and `rag.example.com`.

**Key services:** rag-worker (file indexer), rag-mcp (MCP search server), paperclip-server (Docker image built from `paperclip/` submodule), paperclip-db (PostgreSQL 17), Qdrant (vector DB), Ollama (local LLM).

## Conventions

- All commit messages must be written in English.
- Paperclip runs from a Docker image (`paperclip-server:latest`). After code changes in `paperclip/`, rebuild: `docker build -t paperclip-server:latest paperclip/` then `docker compose up -d paperclip-server`.
- **Deployment workflow:** Changes are first tested by patching files inside running containers (`docker cp`). Once user confirms everything works, the workflow is: (1) copy patches into repo source files, (2) commit and push, (3) rebuild Docker images, (4) `docker compose up -d --force-recreate`. Never leave changes only in containers — they are lost on recreate. Source files in the repo are the source of truth.

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
│ ┌─────────────────┐ │                   │   search_outline                │
│ │ UI (Vite SPA)   │ │                   │   list_outline_documents        │
│ │ (React)         │ │                   ▼                                 │
│ └─────────────────┘ │          ┌──────────────────┐                       │
│ ┌─────────────────┐ │          │      Qdrant      │                       │
│ │ Heartbeat Svc   │ │          │   (:6333/6334)   │                       │
│ │ (cron → runs)   │ │          │                  │                       │
│ └────────┬────────┘ │          │ Collections:     │                       │
│          │          │          │ • outline_docs   │                       │
└──────────┼──────────┘          │ • agent_memory   │                       │
           │                     │ • nextcloud_*    │                       │
           │                     └──────────────────┘                       │
           │                                                                │
           │  POST /v1/runs (SSE)     ┌────────────────────────┐           │
           │  (heartbeat_run_id +     │    hermes-gateway      │           │
           │   pcp_* API key)         │   (Supervisor PID 1)   │           │
           │                     ┌───►│                        │           │
           │                     │    │ ┌────────────────────┐  │           │
           │                     │    │ │ orchestrator.py    │  │           │
           │                     │    │ │ (reconcile 60s)    │  │           │
           └─────────────────────┼───►│ │  • provisioning   │  │           │
                                 │    │ │  • skill_scanner   │  │           │
           ┌─────────────────────┼───►│ │  • skill_git_sync  │  │           │
           │                     │    │ │  • _patch_installed│  │           │
           │                     │    │ └────────────────────┘  │           │
           │  ports.json         │    │                         │           │
           │  (shared volume)    │    │ ┌────────────────────┐  │           │
           │                     │    │ │ gateway × N         │  │           │
           │                     │    │ │ (one per agent)     │  │           │
           │                     │    │ │ api_server.py       │  │           │
           │                     │    │ │ :8642 .. :8673     │  │           │
           │                     │    │ └────────────────────┘  │           │
           │                     │    │                         │           │
           │                     │    │ ┌────────────────────┐  │           │
           │                     │    │ │ session_indexer.py  │  │           │
           │                     │    │ │ (10 min cron,       │  │           │
           │                     │    │ │  embed → Qdrant)    │  │           │
           │                     │    │ └────────────────────┘  │           │
           │                     │    │                         │           │
           │                     │    │ ┌────────────────────┐  │           │
           │                     │    │ │ memory_mcp_server   │  │           │
           │                     │    │ │ (:8680, MCP)        │  │           │
           │                     │    │ │ search_memory       │  │           │
           │                     │    │ │ get_agent_context   │  │           │
           │                     │    │ └────────────────────┘  │           │
           │                     │    │                         │           │
           │                     │    │ ┌────────────────────┐  │           │
           │                     │    │ │ rag-worker          │  │           │
           │                     │    │ │ (Outline + NC sync  │  │           │
           │                     │    │ │  → Qdrant index)    │  │           │
           │                     │    │ └────────────────────┘  │           │
           │                     │    └────────────────────────┘           │
           │                     │                                         │
           │  ┌──────────────────┘                                         │
           ▼  ▼                                                            │
┌──────────────────┐    ┌──────────────┐    ┌──────────────────┐          │
│  paperclip-db    │    │    Ollama     │    │  docker-guard    │          │
│  PostgreSQL 17   │    │   (:11434)   │    │    (:2375)       │          │
│  (:5432)         │    │              │    │                  │          │
│                  │    │ Models:      │    │ Docker API proxy │          │
│ Tables:          │    │ • nomic-embed│    │ (GET filtered)   │          │
│ • issues         │    │   -text 768d │    │ (write blocked)  │          │
│ • agents         │    │ • llama3     │    └────────┬─────────┘          │
│ • company_       │    │ • qwen etc.  │             │                    │
│   memberships    │    └──────────────┘   /var/run/docker.sock:ro        │
│ • company_skills │                                             │         │
│ • heartbeat_runs │    ┌──────────────┐                    Docker daemon │
│ • activity_log   │    │   rag-worker │                                  │
│ • budgets        │    │  (indexer)   │                                  │
│ • budget_        │    │              │                                  │
│   incidents      │    │ Outline API ─┼── sync every 300s ──► Qdrant   │
│ • instance_      │    │ Nextcloud ───┼── sync every 600s ──► Qdrant   │
│   settings       │    └──────────────┘                                  │
└──────────────────┘                                                       │
                                                                           │
┌─── outline_internal network ─────────────────────────────────────────┐   │
│                                                                       │   │
│  ┌──────────────────────────────────┐                                 │   │
│  │       Outline (knowledge base)   │◄── hermes-gateway MCP          │   │
│  │       outline.collaborationism   │    (Bearer ol_api_...)         │   │
│  │       .tech                      │    create/update/search docs   │   │
│  └──────────────────────────────────┘                                 │   │
│                                                                       │   │
└───────────────────────────────────────────────────────────────────────┘   │
                                                                            │
┌─── nextcloud-rag network ─────────────────────────────────────────────┐   │
│                                                                       │   │
│  ┌──────────────────────────────────┐                                 │   │
│  │       Nextcloud (file storage)   │◄── rag-worker (WebDAV)          │   │
│  │                                  │◄── nextcloud-mcp               │   │
│  └──────────────────────────────────┘                                 │   │
│                                                                       │   │
│  ┌──────────────────────────────────┐                                 │   │
│  │       nextcloud-mcp              │◄── hermes-gateway agents        │   │
│  │       (file read/write MCP)      │                                 │   │
│  └──────────────────────────────────┘                                 │   │
│                                                                       │   │
└───────────────────────────────────────────────────────────────────────┘   │
                                                                            │
┌─── External integrations ─────────────────────────────────────────────┐   │
│                                                                       │   │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────────────────┐  │   │
│  │  Telegram  │  │  GitHub    │  │  LLM Providers (credential    │  │   │
│  │  (per-agent│  │  (skill    │  │  pool, rotates on quota)      │  │   │
│  │   bots)    │  │   git sync)│  │  GLM, ZAI, Gemini, OpenRouter │  │   │
│  └────────────┘  └────────────┘  └────────────────────────────────┘  │   │
│                                                                       │   │
└───────────────────────────────────────────────────────────────────────┘   │
                                                                            │
┌─── Shared Docker Volumes ─────────────────────────────────────────────┐   │
│                                                                       │   │
│  paperclip_pgdata  ← paperclip-db persistence                        │   │
│  paperclip_data    ← agent instructions, prompt-template.md (shared) │   │
│  hermes_profiles   ← agent sessions, memories, skills (hermes-gateway)│   │
│  hermes_venv       ← pip-installed hermes-agent (shared build)       │   │
│  hermes_src        ← hermes-agent-build dir (shared copy)            │   │
│  hermes_instances  ← hermes instance data                            │   │
│  gateway_ports     ← ports.json (agent_id → port, shared rw)        │   │
│  qdrant_data       ← vector embeddings persistence                   │   │
│  ollama_data       ← downloaded LLM models                           │   │
│                                                                       │   │
└───────────────────────────────────────────────────────────────────────┘   │
```

### Heartbeat Run Flow

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                        HEARTBEAT RUN FLOW                            │
 │                                                                     │
 │  1. TRIGGER                                                         │
 │  ┌────────────────┐                                                 │
 │  │ paperclip-server│── heartbeat cron ──► create heartbeat_run in DB│
 │  │                │                                (real UUID)      │
 │  └────────────────┘                                                 │
 │         │                                                           │
 │         │ 2. ADAPTER INVOCATION                                     │
 │         │    (in-process, same container)                           │
 │         ▼                                                           │
 │  ┌──────────────────────────┐                                       │
 │  │ hermes-paperclip-adapter │                                       │
 │  │ (execute.ts)             │                                       │
 │  │                          │                                       │
 │  │ • buildInputMessage()    │                                       │
 │  │   ~400 chars task prompt │                                       │
 │  │ • Read ports.json        │                                       │
 │  │   → agent port           │                                       │
 │  │ • POST /v1/runs (SSE)    │──────────────────────────────────┐    │
 │  └──────────────────────────┘                                  │    │
 │                                                                │    │
 │         3. AGENT EXECUTION                                     │    │
 │         ┌──────────────────────────────────────────────────────┘    │
 │         ▼                                                          │
 │  ┌──────────────────────────┐                                      │
 │  │ hermes-gateway           │                                      │
 │  │ api_server.py (:8642+)   │                                      │
 │  │                          │                                      │
 │  │ • Validate pcp_* key     │                                      │
 │  │ • Set HEARTBEAT_RUN_ID   │                                      │
 │  │ • Evict stale MCP conn   │                                      │
 │  │ • AIAgent.run_conversation()                                    │
 │  │   → LLM API call         │                                      │
 │  │   → tool_use loop        │                                      │
 │  │   → text-only retry (×2) │                                      │
 │  └──────────┬───────────────┘                                      │
 │             │                                                      │
 │             │ 4. MCP TOOL CALLS (during agent loop)                 │
 │             │                                                      │
 │             ├──────────────► paperclip-mcp ──► paperclip-server API │
 │             │                  (pcp_* key + run_id)                 │
 │             │                                                      │
 │             ├──────────────► rag-mcp ──► Qdrant (semantic search)  │
 │             │                                                      │
 │             ├──────────────► Outline MCP ──► Outline API            │
 │             │                  (Bearer token)                       │
 │             │                                                      │
 │             ├──────────────► memory MCP (:8680) ──► Qdrant         │
 │             │                  (search_memory)                      │
 │             │                                                      │
 │             ├──────────────► nextcloud-mcp ──► Nextcloud WebDAV    │
 │             │                                                      │
 │             └──────────────► docker-guard ──► Docker daemon        │
 │                                (filtered GET, blocked write)        │
 │                                                                    │
 │         5. RESULT                                                   │
 │         ◄────────────────────────────────────────────────────────── │
 │  ┌──────────────────────────┐                                      │
 │  │ hermes-paperclip-adapter │                                      │
 │  │ • Parse SSE stream       │                                      │
 │  │ • Return resultJson:     │                                      │
 │  │   { summary: "..." }     │                                      │
 │  └──────────┬───────────────┘                                      │
 │             │                                                      │
 │             ▼                                                      │
 │  ┌────────────────┐                                                │
 │  │ paperclip-server│── write result_json to heartbeat_runs         │
 │  │                │── create issue comment (buildHeartbeatRunComment)│
 │  │                │── clear checkoutRunId (release lock)            │
 │  └────────────────┘                                                │
 └─────────────────────────────────────────────────────────────────────┘
```

### Agent Auth Chain

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                     AUTHENTICATION CHAIN                             │
 │                                                                     │
 │  paperclip-server         hermes-gateway          MCP backend       │
 │  ┌────────────┐          ┌──────────────┐        ┌────────────┐    │
 │  │ heartbeat  │── run ──►│ orchestrator │        │paperclip-  │    │
 │  │ service    │  UUID    │              │        │mcp         │    │
 │  │            │          │ reads        │        │            │    │
 │  │ creates    │          │ agent_api_   │        │ receives   │    │
 │  │ heartbeat_ │          │ keys.json    │        │ X-Paperclip│    │
 │  │ run in DB  │          │              │        │ -Api-Key:  │    │
 │  └────────────┘          │ writes pcp_* │        │ pcp_*      │    │
 │                          │ to supervisor│        │            │    │
 │                          │ config as    │        │ X-Paperclip│    │
 │                          │ env var      │        │ -Run-ID:   │    │
 │                          │              │        │ <uuid>     │    │
 │  ┌────────────┐          │ ┌──────────┐ │        │            │    │
 │  │ adapter    │── JWT ──►│ │ gateway  │ │──────►│ forwards   │    │
 │  │ (execute)  │  + runId │ │ process  │ │ HTTP   │ to paperclip│   │
 │  │            │          │ │          │ │       │ server /api│    │
 │  │ gets ctx.  │          │ │ pcp_*    │ │       │            │    │
 │  │ runId +    │          │ │ already  │ │        └─────┬──────┘    │
 │  │ authToken  │          │ │ in env?  │ │              │           │
 │  │ from PCP   │          │ │ → keep   │ │              ▼           │
 │  └────────────┘          │ │ → ignore │ │     ┌────────────┐      │
 │                          │ │ JWT      │ │     │paperclip-  │      │
 │                          │ └──────────┘ │     │server      │      │
 │                          └──────────────┘     │ /api       │      │
 │                                               │            │      │
 │                                               │ validates  │      │
 │                                               │ pcp_* key  │      │
 │                                               │ → agent ID │      │
 │                                               │            │      │
 │                                               │ run_id FK  │      │
 │                                               │ optional   │      │
 │                                               │ (cleaned   │      │
 │                                               │  if stale) │      │
 │                                               └────────────┘      │
 └─────────────────────────────────────────────────────────────────────┘
```

### MCP Tool Chain

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │               MCP SERVERS (hermes-agent perspective)                 │
 │                                                                     │
 │  hermes-gateway agent process                                       │
 │  ┌──────────────────────────────────────────────────────────────┐   │
 │  │                      AIAgent                                  │   │
 │  │                                                                │   │
 │  │  Tool prefix: mcp_<server>_<original_name>                    │   │
 │  │  Example: paperclip_list_issues → mcp_paperclip_paperclip_list_issues│
 │  │                                                                │   │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │   │
 │  │  │ paperclip    │  │ rag          │  │ outline            │  │   │
 │  │  │ (StreamableHTTP)│(StreamableHTTP)│(StreamableHTTP,     │  │   │
 │  │  │              │  │              │  │  external URL)     │  │   │
 │  │  │ 23 tools:    │  │ 3 tools:     │  │ 2 tools:           │  │   │
 │  │  │ • list_issues│  │ • search_*   │  │ • mcp_outline_     │  │   │
 │  │  │ • set_check  │  │ • list_*     │  │   search           │  │   │
 │  │  │ • update_*   │  │              │  │ • mcp_outline_     │  │   │
 │  │  │ • create_*   │  │              │  │   create_document   │  │   │
 │  │  └──────┬───────┘  └──────┬───────┘  └──────────┬─────────┘  │   │
 │  │         │                 │                      │            │   │
 │  │  ┌──────┴───────┐  ┌─────┴────────┐  ┌─────────┴──────────┐ │   │
 │  │  │ memory       │  │ nextcloud    │  │ docker-guard       │ │   │
 │  │  │ (:8680)      │  │              │  │ (Docker API proxy) │ │   │
 │  │  │              │  │ 2 tools:     │  │                    │ │   │
 │  │  │ 2 tools:     │  │ • read_file  │  │ Passthrough:       │ │   │
 │  │  │ • search_    │  │ • write_file │  │ • GET (filtered)   │ │   │
 │  │  │   memory     │  │ • list_files │  │ Blocked:           │ │   │
 │  │  │ • get_agent_ │  │ • download   │  │ • POST/PUT/DELETE  │ │   │
 │  │  │   context    │  │ • upload     │  │   (except allowed) │ │   │
 │  │  └──────────────┘  └──────────────┘  └────────────────────┘ │   │
 │  │                                                                │   │
 │  └──────────────────────────────────────────────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────┘
```

### Data Flows

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │                       INDEXING PIPELINE                              │
 │                                                                     │
 │  ┌──────────────┐         ┌──────────────┐        ┌─────────────┐  │
 │  │   Outline    │── REST ─►│  rag-worker  │─ embed ►│   Qdrant    │  │
 │  │   (API)      │  300s   │  sync_outline│        │outline_docs │  │
 │  └──────────────┘         └──────────────┘        └─────────────┘  │
 │                                                                     │
 │  ┌──────────────┐         ┌──────────────┐        ┌─────────────┐  │
 │  │  Nextcloud   │─ WebDAV ►│  rag-worker  │─ embed ►│   Qdrant    │  │
 │  │              │  600s   │  sync_NC     │        │nextcloud_*  │  │
 │  └──────────────┘         └──────────────┘        └─────────────┘  │
 │                                                                     │
 │  ┌──────────────┐         ┌──────────────┐        ┌─────────────┐  │
 │  │ Agent        │─ JSONL ─►│ session_     │─ embed ►│   Qdrant    │  │
 │  │ sessions +   │  10min  │  indexer     │        │agent_memory │  │
 │  │ MEMORY.md    │         │  (nomic-768d)│        │             │  │
 │  └──────────────┘         └──────────────┘        └─────────────┘  │
 │                                                                     │
 │  Embeddings: Ollama nomic-embed-text (768d, cosine)                │
 └─────────────────────────────────────────────────────────────────────┘

 ┌─────────────────────────────────────────────────────────────────────┐
 │                       SKILL LIFECYCLE                                │
 │                                                                     │
 │  Sources:                                                           │
 │  ┌────────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
 │  │ /opt/skills    │  │ /opt/hermes-   │  │ /opt/hermes-agent/   │  │
 │  │ (project,     │  │ agent/skills   │  │ optional-skills      │  │
 │  │  highest prio)│  │ (73 built-in)  │  │ (46 optional)        │  │
 │  └───────┬────────┘  └───────┬────────┘  └──────────┬───────────┘  │
 │          │                   │                       │              │
 │          └───────────────────┼───────────────────────┘              │
 │                              ▼                                      │
 │                  ┌───────────────────────┐                          │
 │                  │  skill_importer.py    │                          │
 │                  │  → company_skills DB  │                          │
 │                  │  (sourceKind:         │                          │
 │                  │   hermes_bundled)     │                          │
 │                  └───────────┬───────────┘                          │
 │                              │                                      │
 │          ┌───────────────────┼───────────────────────┐              │
 │          ▼                   ▼                       ▼              │
 │  ┌──────────────┐  ┌──────────────────┐  ┌───────────────────┐    │
 │  │ Agent creates │  │ skill_scanner.py │  │ skill_git_sync.py │    │
 │  │ skill via     │  │ (discovers new   │  │ (bidirectional    │    │
 │  │ skill_manage  │  │  SKILL.md in     │  │  GitHub ↔ DB)     │    │
 │  │ tool          │  │  profiles/)      │  │                   │    │
 │  └──────┬───────┘  └──────┬───────────┘  └──────┬────────────┘    │
 │         │                 │                      │                  │
 │         ▼                 ▼                      ▼                  │
 │         ┌─────────────────────────────────────────┐                │
 │         │        company_skills (PostgreSQL)       │                │
 │         │                                          │                │
 │         │  sourceKind: hermes_bundled | agent_created│ git_sync     │
 │         │  source_type: catalog (NULL locator)      │              │
 │         └──────────────────────────────────────────┘                │
 │                              │                                      │
 │                              ▼                                      │
 │                  ┌───────────────────────┐                          │
 │                  │ _sync_agent_skills()  │                          │
 │                  │ (per-agent sync)      │                          │
 │                  │                       │                          │
 │                  │ hermes skills → symlink│                          │
 │                  │ PCP skills → write file│                          │
 │                  │ stale → remove        │                          │
 │                  └───────────────────────┘                          │
 └─────────────────────────────────────────────────────────────────────┘
```

### Hermes Gateway Internal Structure

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │              hermes-gateway (single Docker container)                │
 │                                                                     │
 │  PID 1: supervisord                                                 │
 │  ┌─────────────────────────────────────────────────────────────┐   │
 │  │                                                             │   │
 │  │  ┌────────────────────────────────────────────────────────┐ │   │
 │  │  │  orchestrator.py (main process)                        │ │   │
 │  │  │                                                        │ │   │
 │  │  │  Reconcile loop (60s):                                 │ │   │
 │  │  │  1. Query PostgreSQL → active agents                   │ │   │
 │  │  │     (company_memberships WHERE principal_type='agent'   │ │   │
 │  │  │      AND adapter_type='hermes_local')                  │ │   │
 │  │  │  2. Read agent instructions from paperclip_data vol    │ │   │
 │  │  │  3. Generate config.yaml per agent (config_generator)  │ │   │
 │  │  │  4. Write ports.json → shared volume                   │ │   │
 │  │  │  5. supervisorctl reread + update (hot-reload)         │ │   │
 │  │  │  6. _sync_agent_skills() → symlinks + file writes      │ │   │
 │  │  │  7. skill_importer → company_skills upsert             │ │   │
 │  │  │  8. skill_scanner → agent-created skills upsert        │ │   │
 │  │  │  9. skill_git_sync → GitHub push/pull                  │ │   │
 │  │  │  10. Hot-reload: hash fingerprint of config template   │ │   │
 │  │  │      + orchestrator + config_generator                  │ │   │
 │  │  └────────────────────────────────────────────────────────┘ │   │
 │  │                                                             │   │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │   │
 │  │  │ gateway-A    │  │ gateway-B    │  │ gateway-...  │      │   │
 │  │  │ :8642        │  │ :8643        │  │ :8644..8673  │      │   │
 │  │  │              │  │              │  │              │      │   │
 │  │  │ api_server.py│  │ api_server.py│  │              │      │   │
 │  │  │ run_agent.py │  │ run_agent.py │  │              │      │   │
 │  │  │              │  │              │  │              │      │   │
 │  │  │ profile:     │  │ profile:     │  │              │      │   │
 │  │  │ ~/.hermes/   │  │ ~/.hermes/   │  │              │      │   │
 │  │  │ profiles/    │  │ profiles/    │  │              │      │   │
 │  │  │ <agentId>/   │  │ <agentId>/   │  │              │      │   │
 │  │  │ ├ config.yaml│  │ ├ config.yaml│  │              │      │   │
 │  │  │ ├ SOUL.md   │  │ ├ SOUL.md   │  │              │      │   │
 │  │  │ ├ skills/   │  │ ├ skills/   │  │              │      │   │
 │  │  │ ├ sessions/ │  │ ├ sessions/ │  │              │      │   │
 │  │  │ └ memories/ │  │ └ memories/ │  │              │      │   │
 │  │  └──────────────┘  └──────────────┘  └──────────────┘      │   │
 │  │                                                             │   │
 │  │  ┌────────────────────┐    ┌───────────────────────────┐   │   │
 │  │  │ session_indexer.py │    │ memory_mcp_server.py      │   │   │
 │  │  │ (supervisor proc)  │    │ (supervisor proc, :8680)  │   │   │
 │  │  │                    │    │                           │   │   │
 │  │  │ Every 10 min:      │    │ MCP StreamableHTTP:       │   │   │
 │  │  │ scan profiles/*/   │    │ • search_memory(query)    │   │   │
 │  │  │  sessions/*.jsonl  │    │ • get_agent_context(name) │   │   │
 │  │  │ + memories/*.md    │    │                           │   │   │
 │  │  │ → embed → Qdrant   │    │ Queries Qdrant           │   │   │
 │  │  └────────────────────┘    │ agent_memory collection   │   │   │
 │  │                            └───────────────────────────┘   │   │
 │  │                                                             │   │
 │  │  Mounted volumes:                                           │   │
 │  │  • paperclip_data:/paperclip:ro (instructions)             │   │
 │  │  • hermes_profiles:/root/.hermes/profiles (rw)             │   │
 │  │  • hermes_src:/opt/hermes-agent-build                      │   │
 │  │  • ./hermes-agent:/opt/hermes-agent:ro (submodule)         │   │
 │  │  • ./hermes-gateway/skills:/opt/skills:ro                  │   │
 │  │  • gateway_ports:/run/gateway-ports (ports.json rw)        │   │
 │  │  • agent_api_keys.json:ro (pcp_* permanent keys)           │   │
 │  │                                                            │   │
 │  └─────────────────────────────────────────────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────┘
```

### Paperclip Server Internal Structure

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │  paperclip-server (Docker image: paperclip-server:latest)           │
 │                                                                     │
 │  Entrypoint: /opt/paperclip-entrypoint.sh (patches on start)       │
 │  Command: node server/dist/index.js (:3100)                        │
 │                                                                     │
 │  ┌─────────────────────────────────────────────────────────────┐   │
 │  │  Express Server                                              │   │
 │  │                                                             │   │
 │  │  /api/*                                                     │   │
 │  │  ├── auth (better-auth, JWT, session cookies)               │   │
 │  │  ├── agents (CRUD, instructions, skills sync)               │   │
 │  │  ├── issues (CRUD, checkout/release, checklist)             │   │
 │  │  ├── heartbeat (cron → create runs → invoke adapter)        │   │
 │  │  ├── companies/:id/skills (list, visibility, sources)       │   │
 │  │  ├── instance/settings (timezone, skills-sync config)       │   │
 │  │  └── budgets (policies, incidents, metrics)                 │   │
 │  │                                                             │   │
 │  │  UI (Vite SPA)                                              │   │
 │  │  └── express.static → /app/ui/dist                         │   │
 │  └─────────────────────────────────────────────────────────────┘   │
 │                                                                     │
 │  ┌─────────────────────────────────────────────────────────────┐   │
 │  │  hermes-paperclip-adapter (bind-mounted, ro)                 │   │
 │  │  node_modules/.pnpm/hermes-paperclip-adapter@0.2.0/         │   │
 │  │  ├── dist/server/execute.js ← gateway mode, resultJson      │   │
 │  │  ├── dist/server/skills.js  ← skill snapshot                │   │
 │  │  └── dist/server/index.js   ← re-exports                    │   │
 │  └─────────────────────────────────────────────────────────────┘   │
 │                                                                     │
 │  Patches (applied by entrypoint.sh, survive rebuild):              │
 │  ├── server/dist/services/heartbeat.js (checkoutRunId cleanup)     │
 │  ├── server/dist/routes/company-skills.js (hermes_bundled case)    │
 │  └── server/dist/services/company-skills.js (deriveSkillSourceInfo)│
 │                                                                     │
 │  Patches (bind-mounted from ./patches/, survive rebuild):          │
 │  ├── server/dist/routes/company-skills.js                          │
 │  └── server/dist/services/company-skills.js                        │
 │                                                                     │
 │  Shared volumes:                                                    │
 │  • paperclip_data:/paperclip (agent instructions, prompt-template) │
 │  • gateway_ports:/run/gateway-ports:ro (read ports.json)           │
 │  • hermes_instances:/paperclip/hermes-instances                     │
 │  • ./paperclip/ui/dist:/app/ui/dist (rw, Vite output)             │
 │  • ./paperclip/ui/src:/app/ui/src:ro (for in-container rebuild)    │
 │  • ./paperclip/packages/shared/src:ro                               │
 │  • ./paperclip/packages/db/src:ro                                   │
 └─────────────────────────────────────────────────────────────────────┘
```

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

### Agent Skills

Hermes-agent имеет систему навыков (SKILL.md) с progressive disclosure: `skills_list` → `skill_view`.

- **Источники навыков** — `skill_importer.py` в оркестраторе сканирует 3 директории:
  - `/opt/skills` — кастомные навыки проекта (docker-management для docker-guard) — **приоритетнее остальных**
  - `/opt/hermes-agent/skills` — 73 встроенных навыка (software-development, devops, github, research, mlops и т.д.)
  - `/opt/hermes-agent/optional-skills` — 46 опциональных (blockchain, security и т.д.)
- Приоритет: если slug дублируется (например `docker-management`), первый найденный (из `/opt/skills`) побеждает
- Навыки импортируются в `company_skills` БД при старте оркестратора (ключ: `hermes/hermes-agent/<category>/<slug>`, sourceKind: `hermes_bundled`)
- `source_type='catalog'`, `source_locator=NULL` — чтобы избежать `pruneMissingLocalPathSkills()` и `resolveLocalSkillFilePath()` ENOENT
- Метка источника хранится в `metadata.sourceLabel` ("Hermes Agent", "Hermes Agent (optional)", "Project skills"), путь — в `metadata.sourcePath`
- Server-side: `deriveSkillSourceInfo()` в `company-skills.js` патчен для `metadata.sourceKind === "hermes_bundled"` — возвращает `sourceLabel` из metadata, badge "catalog", `sourcePath: null`
- Импорт выполняется для ВСЕХ компаний в БД (upsert, INSERT ON CONFLICT UPDATE)
- **`_sync_agent_skills()`** — читает `paperclipSkillSync.desiredSkills` из `adapter_config` агента и:
  - Создаёт **symlinks** для hermes-навыков (путь существует в контейнере)
  - Пишет **файлы из БД** для paperclip-bundled навыков (путь `/app/skills/...` недоступен в hermes-gateway)
  - Удаляет stale symlinks/файлы при каждом sync
- CEO управляет навыками per-agent через UI → Paperclip хранит в `adapter_config.paperclipSkillSync.desiredSkills`
- Агенты видят только включённые навыки (loaded from profile `skills/` dir)
- `external_dirs` **удалён** из `config-template.yaml` — навыки загружаются только из профиля
- **Docker skill** (`hermes-gateway/skills/devops/docker-management/SKILL.md`) — кастомный навык на русском для docker-guard
- Навыки монтируются read-only через `./hermes-gateway/skills:/opt/skills:ro` в docker-compose.yml
- `queryKeys.ts` в контейнере может быть устаревшим — UI src НЕ bind-mounted. После изменений в `paperclip/ui/src/lib/queryKeys.ts` нужен `docker cp` + vite build + bump `sw.js` CACHE_NAME

### Agent Memory Service

Векторизованная память агентов — session history и MEMORY.md → Qdrant, доступ через MCP tools.

- **session_indexer.py** — Supervisor процесс в hermes-gateway. Каждые 10 мин сканирует `profiles/*/sessions/*.jsonl` и `memories/MEMORY.md`, извлекает assistant-сообщения, эмбеддит через Ollama (nomic-embed-text, 768d), upsert в Qdrant collection `agent_memory`
- **memory_mcp_server.py** — MCP StreamableHTTP server на порту 8680. Tools: `search_memory(query)`, `get_agent_context(agent_name)`
- Индексер отслеживает файлы по mtime+size хэшу (state: `profiles/indexer-state.json`). При ошибке embed файл НЕ помечается обработанным — retry на следующем цикле
- BATCH_SIZE=1, MAX_TEXT_LEN=1000 — Ollama nomic-embed-text нестабилен на больших батчах/текстах
- Коллекция Qdrant `agent_memory`: 768d cosine, payload indexes на `agent_name` (keyword), `source` (keyword)
- Профили агентов персистятся через Docker volume `hermes_profiles` → `/root/.hermes/profiles`
- Конфигурация: `memory` mcp_server в `config-template.yaml` / `config.yaml`, переменные `OLLAMA_BASE_URL`, `QDRANT_URL`, `EMBED_MODEL`, `MEMORY_API_KEY`

### Agent Skill Writeback

Оркестратор автоматически обнаруживает навыки, созданные агентами через `skill_manage` tool, и персистит их в `company_skills` БД. Поддерживается двунаправленная синхронизация с git-репозиторием.

- **skill_scanner.py** — сканирует `profiles/{agentId}/skills/` на предмет новых/изменённых SKILL.md (не symlinks)
  - Пропускает symlinks (hermes-bundled навыки, подмонтированные `_sync_agent_skills()`)
  - Пропускает навыки с slug, совпадающим с hermes-bundled (DB-sourced навыки, записанные `_sync_agent_skills()`)
  - Отслеживает mtime+size хэш в `profiles/skill-scanner-state.json` — неизменённые файлы пропускаются
  - DB key: `agent/{agent_id}/{category}/{slug}`, metadata.sourceKind: `agent_created`
  - metadata включает: `authorAgentId`, `authorAgentName`, `category`
  - `source_type='catalog'`, `source_locator=NULL` — аналогично hermes_bundled навыкам
- **skill_git_sync.py** — `SkillGitSync` класс для двунаправленной синхронизации с git repo
  - **Push**: `company_skills` (sourceKind in `agent_created`, `git_sync`) → SKILL.md в repo. Удаляет orphaned-файлы.
  - **Pull**: SKILL.md из repo → `company_skills` (sourceKind: `git_sync`). Скрывает (hidden=true) навыки, удалённые из repo.
  - Auth: HTTPS + PAT (embedded в URL: `https://{token}@github.com/...`)
  - Git author через `GIT_AUTHOR_NAME`/`GIT_AUTHOR_EMAIL` env vars
  - Клонирует в `/tmp/skill-git-sync-repo`, при повторных запусках делает `git pull --rebase`
- **Настройки**: `instance_settings.skills_sync` jsonb (repoUrl, branch, path, token, author)
  - API: `GET/PATCH /api/instance/settings/skills-sync`, `POST .../trigger`
  - UI: секция "Skill Repository" в Instance Settings (InstanceGeneralSettings.tsx)
  - Orchestrator читает из БД напрямую при каждом reconcile cycle
- **Server-side**: `deriveSkillSourceInfo()` в `company-skills.ts` — два новых случая:
  - `metadata.sourceKind === "agent_created"` → badge `agent_created`, label `Agent: {name}`, read-only
  - `metadata.sourceKind === "git_sync"` → badge `github`, label "Git Sync" или repo URL, read-only
- **UI badge**: `CompanySkills.tsx` — `Bot` icon для `agent_created` badge, группировка по `sourceLabel` (имя агента)
- **E2E tests**: `e2e/test_skills_api.py` (13 API route tests), `e2e/test_skills_ui.py` (30 UI tests) — все httpx + Playwright
- **Test infrastructure**: `docker-compose.test.yml` (port 3100, `authenticated` mode), `e2e/patch_test.sh`, `e2e/seed_skills_e2e.sql`

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
- **UI src устаревает в контейнере** — после `docker compose up -d --build` контейнер получает старые исходники из образа. Нужно `docker cp` ВСЕ изменённые файлы (`queryKeys.ts`, `companySkills.ts`, и т.д.) перед каждым `vite build`
- `pruneMissingLocalPathSkills()` в `company-skills.ts` — при каждом `GET /companies/:id/skills` сервер проверяет `source_type='local_path'` навыки: если `source_locator` не существует на диске контейнера paperclip-server, навык **удаляется из БД**. Решение: использовать `source_type='catalog'` для навыков, чьи файлы недоступны в paperclip-server
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
- `hermes-gateway/orchestrator/skill_scanner.py` — profile scanner + DB upsert for agent-created skills
- `hermes-gateway/orchestrator/skill_git_sync.py` — `SkillGitSync` bidirectional git push/pull
- `hermes-gateway/supervisord.conf` — session-indexer + memory-mcp programs
- `hermes-gateway/config-template.yaml` — agent config template, includes `skills.external_dirs`
- `hermes-gateway/skills/docker-management/SKILL.md` — custom Docker skill (docker-guard proxy, allowed containers)
- `hermes-gateway/tests/test_skill_scanner.py` — 18 tests for agent skill discovery
- `hermes-gateway/tests/test_skill_git_sync.py` — 10 tests for git sync
- `docker-compose.yml` — ui/dist bind mount, hermes-gateway service, adapter bind mounts, hermes_profiles volume, skills mount

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
- `paperclip/ui/src/pages/InstanceGeneralSettings.tsx` — Regional block (timezone + 24h) + Skill Repository section (git sync settings)
- `paperclip/ui/src/pages/CompanySkills.tsx` — `Bot` icon badge for `agent_created`, skill grouping by `sourceLabel`
- `paperclip/ui/src/lib/utils.ts` — formatDateTime/formatDate с timezone opts
- `paperclip/ui/src/hooks/useTimeSettings.ts` — timezone, timeFormat hooks
- `paperclip/ui/src/components/CommentThread.tsx` — использует `useTimeSettings()` для 24h/12h
- `paperclip/ui/src/components/transcript/RunTranscriptView.tsx` — timestamps, Brain icon, filters
- `paperclip/ui/src/components/IssueProperties.tsx` — checklist rendering (CheckSquare/Square, progress)
- `paperclip/ui/src/components/PropertiesPanel.tsx` — "Details" panel title
- `paperclip/ui/src/api/companySkills.ts` — client API: `setVisibility`, `deleteBySource`, `listIncludingHidden`

### Paperclip Server (modified):
- `paperclip/server/src/services/budgets.ts` — `migratePoliciesMetric` деактивирует вместо DELETE
- `paperclip/server/src/services/instance-settings.ts` — timezone/timeFormat defaults + `skillsSync` get/update
- `paperclip/server/src/services/company-skills.ts` — `agent_created`/`git_sync` cases in `deriveSkillSourceInfo`, `setVisibility`, `deleteBySource`
- `paperclip/server/src/routes/company-skills.ts` — all skill routes: setVisibility, deleteBySource, hiddenSources, teamSkills
- `paperclip/server/src/routes/instance-settings.ts` — skillsSync GET/PATCH/trigger endpoints

### Shared package (modified):
- `paperclip/packages/shared/src/types/instance.ts` — TimeFormat type, timezone/timeFormat fields
- `paperclip/packages/shared/src/validators/instance.ts` — timezone/timeFormat zod schemas
- `paperclip/packages/shared/src/types/issue.ts` — IssueChecklistItem type, checklist field
- `paperclip/packages/shared/src/validators/issue.ts` — issueChecklistItemSchema, issueChecklistSchema

### E2E Tests:
- `e2e/test_skills_ui.py` — 30 Playwright UI tests (sidebar, filter, source groups, icons, detail pane, skill tree)
- `e2e/test_skills_api.py` — 13 API route tests (setVisibility, deleteBySource, hiddenSources, teamSkills, deleteSkill)
- `e2e/seed_skills_e2e.sql` — Comprehensive seed data for test instance
- `e2e/patch_test.sh` — Script to sync production patches into test container
- `docker-compose.test.yml` — Test Paperclip instance (DB on 5434, server on 3100, `authenticated` mode)

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
 | 4 | `read_file` внутри `execute_code` — если файл уже читался обычным `read_file`, возвращает «File unchanged» вместо контента | **FIXED** — RPC dispatch clears dedup before sandbox read_file |
 | 5 | `read_file` мягкое предупреждение на 3+ одинаковых вызовов подряд — контент возвращается, но warning шумит | **FIXED** — порог повышен с 3→5 (warning), 4→6 (block) |
 | 6 | `delegate_task` не подставляет плейсхолдеры — `{{VARIABLE}}` передаётся как literal текст | **BY DESIGN** — schema не обещает template substitution. Встраивать данные прямо в goal/context |
 | 7 | `set_checklist()` через MCP → не записывается | **FIXED** — `checklist` добавлен в `updateIssueSchema` (shared/validators/issue.ts) |

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

### Skill files endpoint 500 (исправлено)

**Симптом:** `GET /api/companies/:id/skills/:skillId/files?path=SKILL.md` → 500 ENOENT для hermes catalog-навыков.

**Root cause:** `resolveLocalSkillFilePath()` использует `source_locator` как путь. При `source_locator="Hermes Agent (optional)"` → `/app/Hermes Agent (optional)/SKILL.md` → ENOENT. Fallback на `skill.markdown` не срабатывает — `readFile()` выбрасывает исключение ДО достижения else-branch.

**Fix:** `source_locator=NULL` → `normalizeSkillDirectory()` возвращает null → `resolveLocalSkillFilePath()` возвращает null → `readFile()` использует `skill.markdown` из БД. Метка источника перенесена в `metadata.sourceLabel`.

### Skills sync 500 (исправлено)

**Симптом:** `POST /api/agents/:id/skills/sync` → 500 `Cannot read properties of undefined (reading 'length')` — `snapshot.entries.length`.

**Root cause:** `hermes-paperclip-adapter/dist/server/index.js` экспортировал `listSkills`/`syncSkills` с неправильным форматом ответа (`{ desiredSkills, persistedSkills }` вместо `{ entries, warnings, supported, mode }`). Правильная реализация — в `skills.js` (`buildHermesSkillSnapshot`).

**Fix:** `index.js` теперь реэкспортирует `listHermesSkills`/`syncHermesSkills` из `./skills.js`. Исходник `src/server/index.ts` обновлён соответственно.

### Missing server routes (исправлено)

- `/api/companies/:id/team-skills` — отсутствовал → 404. Добавлен stub: `res.json([])`
- `/api/companies/:id/hidden-sources` — отсутствовал → 404. Добавлен stub: `res.json([])`
- Оба маршрута используют `assertCompanyAccess()` для авторизации
- Патчи в `/app/server/dist/routes/company-skills.js` — переживают restart, но НЕ переживают `docker compose up -d --build`

### Server patches persistence

Патчи в `/app/server/dist/` внутри контейнера paperclip-server:
- Переживают: `docker compose restart`
- НЕ переживают: `docker compose up -d --build` (image rebuild)
- Патченные файлы: `company-skills.js` (hermes_bundled case, stub routes), `company-skills.js` в services
- Нет entrypoint script — PID 1 запускает `node server/dist/index.js` напрямую

### Docker-guard container list filtering (исправлено)

**Симптом:** Агент видит ВСЕ 49 контейнеров через docker-guard, хотя write-операции блокируются корректно.

**Root cause:** `guard.py` пропускал все GET-запросы без фильтрации (security model: "read-only unrestricted"). `GET /containers/json` возвращал полный список контейнеров.

**Fix:** Добавлена `_filtered_container_list()` — при `GET /containers/json`.guard запрашивает полный список у Docker, фильтрует по `ALLOWED_LABELS` и `ALLOWED_PREFIXES`, возвращает только разрешённые контейнеры (3 из 49). Остальные GET-эндпоинты (`/_ping`, `/version`, `/images/json`) пропускаются без фильтрации.

**Текущий scope:** `ALLOWED_LABELS=docker-guard.allow`, `ALLOWED_PREFIXES=` (empty) — агент видит только grocy, grocy-shopping-agent, mail-receipts.

### E2E tests run on test instance (IMPORTANT)

Все E2E тесты запускать **только на тестовом инстансе** (`docker-compose.test.yml`, порт 3100, DB на 5434). Никогда не запускать тесты на production (`paperclip-server`). Тестовая БД: `paperclip_test` на `hw-rnd-ai-crew-paperclip-test-db-1`.

### Test instance auth (IMPORTANT)

Тестовый Paperclip (`docker-compose.test.yml`) работает в `authenticated` режиме:
- **Secure cookie**: `__Secure-better-auth.session_token` имеет `Secure` флаг — браузер (и httpx cookie jar) НЕ отправляет его по HTTP. Решение: вручную выставлять `Cookie` заголовок в каждом запросе
- **Origin check**: mutation-запросы (PATCH/PUT/DELETE) требуют `Origin` заголовок — без него 403 "Board mutation requires trusted browser origin"
- **Duplicate user bug**: better-auth создаёт нового user при каждом `sign-in/email` если пользователь ещё не существует. Если seed SQL создал пользователя раньше — получится два user с одним email. Seed SQL (`seed_skills_e2e.sql`) теперь_grants membership для всех пользователей с `test@test.com`
- **API tests**: `e2e/test_skills_api.py` использует `httpx` (не Playwright) с ручным Cookie+Origin — avoids asyncio event loop conflict с Playwright sync API

### deleteBySource NULL sourceLocator bug

`deleteBySource(companyId, sourceType, sourceLocator)` в `company-skills.ts` использует `eq(companySkills.sourceLocator, sourceLocator)`. Для catalog/hermes_bundled навыков `source_locator=NULL` в БД. SQL `= ''` не матчит NULL — нужно `IS NULL`. Patched route validation to only require `sourceType` (not `sourceLocator`), but the service-level query still can't match NULL locators. Workaround: delete by `local_path` source type (which has non-null locators).

### hidden-sources DB schema missing

`companies.hidden_sources` column exists in PostgreSQL but NOT in drizzle schema (`companies.ts`). Routes `GET/PUT /hidden-sources` fail with drizzle errors (`query.getSQL is not a function`, `Cannot convert undefined or null to object`). Fix in test container: patch routes to use drizzle `sql` template literals for raw SQL. Patch in `e2e/patch_test.sh` step 4b (applied after esbuild rebuild).
