# HW RND AI Crew

Infrastructure for the AI crew: RAG pipeline over Nextcloud files, Paperclip server, and Hermes agent integration.

## Architecture

```
                        ┌─────────────┐
                        │   Traefik   │
                        │  (reverse   │
                        │   proxy)    │
                        └──────┬──────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────┴────────┐  ┌───┴───┐  ┌─────────┴─────────┐
     │   rag.collab... │  │ paper │  │ nextcloud.collab..│
     │   rag-mcp:8081  │  │ clip  │  │   (nginx+fpm)     │
     └────────┬────────┘  └───┬───┘  └─────────┬─────────┘
              │                │                │
     ┌────────┴────────┐  ┌───┴────┐           │ webhooks
     │   rag-worker     │  │ paper- │           │ (cron/5min)
     │   :8080          │  │ clip-db│           │
     └──┬─────┬────────┘  └────────┘           │
        │     │                                  │
   ┌────┴──┐  │         nextcloud-rag network ◄──┘
   │Qdrant │  │
   │ :6333 │  │
   └───────┘  │
         ┌────┴─────┐
         │  Ollama   │
         │  :11434   │
         └──────────┘
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| **rag-worker** | 8080 | FastAPI webhook receiver. Downloads files from Nextcloud via WebDAV, parses (PDF, txt, md, csv), chunks, embeds (BGE-large-en-v1.5), stores in Qdrant |
| **rag-mcp** | 8081 | MCP server exposing `search_library`, `list_indexed_files`, `get_file_status`. SSE (`/sse`) + Streamable HTTP (`/mcp`) transports with Bearer auth |
| **qdrant** | 6333 | Vector database for indexed document chunks |
| **ollama** | 11434 | Local LLM runtime |
| **paperclip-server** | 3100 | Paperclip AI platform |
| **paperclip-db** | 5432 | PostgreSQL 17 for Paperclip |

## Submodules

| Submodule | Repo | Description |
|-----------|------|-------------|
| `rag-worker/` | [VladimirHulagov/rag-worker](https://github.com/VladimirHulagov/rag-worker) | File indexer: parsers, chunker, embedder, Qdrant client, FastAPI webhook handler |
| `rag-mcp/` | [VladimirHulagov/rag-mcp](https://github.com/VladimirHulagov/rag-mcp) | MCP server: SSE + Streamable HTTP, Bearer auth, semantic search tools |
| `hermes-agent/` | [VladimirHulagov/hermes-agent](https://github.com/VladimirHulagov/hermes-agent) | Hermes AI agent (MCP client for RAG) |
| `paperclip/` | [VladimirHulagov/paperclip](https://github.com/VladimirHulagov/paperclip) | Paperclip platform |
| `hermes-paperclip-adapter/` | [VladimirHulagov/hermes-paperclip-adapter](https://github.com/VladimirHulagov/hermes-paperclip-adapter) | Hermes-Paperclip adapter |
| `superpowers/` | [VladimirHulagov/superpowers](https://github.com/VladimirHulagov/superpowers) | Agent skills and superpowers |

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

### File Flow

1. File uploaded to Nextcloud (via WebDAV or UI)
2. Nextcloud webhook fires (NodeCreatedEvent / NodeWrittenEvent / NodeDeletedEvent)
3. `rag-worker` receives webhook, downloads file via WebDAV
4. File is parsed (PDF via opendataloader-pdf, plaintext for txt/md/csv)
5. Text is chunked (512 words, 64 overlap)
6. Chunks are embedded with BGE-large-en-v1.5 (1024 dim, CPU-only PyTorch)
7. Vectors upserted to Qdrant

### Nextcloud Webhook Setup

1. In Nextcloud, go to Settings → Workflow engine (requires `webhook_listeners` app)
2. Or via OCS API:

```bash
EVENTS=("NodeCreatedEvent" "NodeWrittenEvent" "NodeDeletedEvent")
for EVENT in "${EVENTS[@]}"; do
  curl -X POST \
    "https://nextcloud.example.com/ocs/v1.php/apps/webhook_listeners/api/v1/webhooks" \
    -u "user:password" \
    -H "OCS-APIRequest: true" \
    -H "Content-Type: application/json" \
    -d "{
      \"httpMethod\": \"POST\",
      \"uri\": \"http://rag-worker:8080/webhook/nextcloud\",
      \"event\": \"OCP\\\\Files\\\\Events\\\\Node\\\\${EVENT}\",
      \"authMethod\": \"header\",
      \"authData\": {\"header\": \"X-Webhook-Secret\", \"value\": \"your-secret\"}
    }"
done
```

3. Allow local remote servers in Nextcloud:

```bash
docker exec nextcloud php occ config:system:set allow_local_remote_servers --value=true --type=boolean
```

4. Set up cron for background jobs (webhooks fire via cron):

```bash
docker exec nextcloud-web crontab -l > /tmp/cron
echo "*/5 * * * * curl -sf http://localhost/cron.php > /dev/null 2>&1" >> /tmp/cron
docker exec -i nextcloud-web crontab - < /tmp/cron
```

### MCP Tools

The rag-mcp server exposes 3 tools via MCP protocol:

| Tool | Description |
|------|-------------|
| `search_library` | Semantic search over indexed documents. Returns matching chunks with score, file, page |
| `list_indexed_files` | List all indexed files with metadata and chunk counts |
| `get_file_status` | Check if a specific file is indexed and its chunk count |

**Endpoints:**
- Streamable HTTP: `POST https://rag.example.com/mcp`
- SSE: `GET https://rag.example.com/sse` + `POST https://rag.example.com/messages/`

Both require `Authorization: Bearer <token>` header.

### Hermes Integration

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  rag:
    url: "https://rag.example.com/mcp"
    headers:
      Authorization: "Bearer ${MCP_RAG_API_KEY}"
    enabled: true
    timeout: 120
    connect_timeout: 60
```

Add to `~/.hermes/.env`:

```
MCP_RAG_API_KEY=your-bearer-token
```

Tools will be available as `mcp_rag_search_library`, `mcp_rag_list_indexed_files`, `mcp_rag_get_file_status`.

## Supported File Types

| Extension | Parser | Notes |
|-----------|--------|-------|
| `.pdf` | opendataloader-pdf | Requires JRE in container |
| `.txt` | Plaintext | |
| `.md` | Plaintext | |
| `.rst` | Plaintext | |
| `.csv` | Plaintext | |

## Tech Stack

- **Python 3.11** (rag-worker, rag-mcp)
- **FastAPI + Uvicorn** (rag-worker webhook)
- **MCP SDK 1.27** (rag-mcp server)
- **Qdrant** (vector DB)
- **sentence-transformers** (BGE-large-en-v1.5, 1024 dim)
- **PyTorch CPU-only** (no GPU required)
- **Nextcloud 31** (file storage + webhook triggers)
- **Traefik** (TLS termination, routing)
- **Docker Compose** (orchestration)
