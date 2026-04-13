# Agents

## Project Overview

HW RND AI Crew is a Docker Compose stack providing RAG over Nextcloud files, Paperclip (AI agent control plane), and Hermes agent integration. Traefik handles TLS/routing. Services run on an internal network behind `paperclip.collaborationism.tech` and `rag.collaborationism.tech`.

**Key services:** rag-worker (file indexer), rag-mcp (MCP search server), paperclip-server (Docker image built from `paperclip/` submodule), paperclip-db (PostgreSQL 17), Qdrant (vector DB), Ollama (local LLM).

## Conventions

- All commit messages must be written in English.
- Paperclip runs from a Docker image (`paperclip-server:latest`). After code changes in `paperclip/`, rebuild: `docker build -t paperclip-server:latest paperclip/` then `docker compose up -d paperclip-server`.
