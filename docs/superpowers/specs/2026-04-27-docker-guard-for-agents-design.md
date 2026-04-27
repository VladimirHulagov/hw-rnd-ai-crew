# Docker-Guard for Paperclip Agents

**Date:** 2026-04-27
**Status:** Approved

## Goal

Give selected Paperclip agents full Docker access to manage `suckless-shopping` containers (grocy, grocy-shopping-agent, mail-receipts) — restart, logs, stop, start, exec — through a label-enforcing Docker API proxy (docker-guard).

## Requirements

1. **Full Docker access** to allowed containers (not just restart)
2. **Per-agent access control** — only agents with `adapter_config.docker.enabled=true` get `DOCKER_HOST`
3. **Configurable allowed containers** — controlled via container labels, not hardcoded names
4. **docker-guard as submodule** — synced from https://github.com/VladimirHulagov/docker-guard

## Architecture

```
Agent (terminal tool, DOCKER_HOST=tcp://docker-guard:2375)
  → docker-guard (label-enforcing proxy, checks docker-guard.allow label)
    → Docker daemon (/var/run/docker.sock)
      → Allowed containers (label: docker-guard.allow=true)
```

## Components

### 1. docker-guard (submodule)

**Location:** `./docker-guard` (git submodule from https://github.com/VladimirHulagov/docker-guard)

**Changes to guard.py:** Replace hardcoded constants with env var overrides:

| Env var | Default | Description |
|---------|---------|-------------|
| `ALLOWED_LABELS` | `hermes-test` | Comma-separated label keys. Container passes check if any of these labels is set to `"true"` |
| `ALLOWED_PREFIXES` | `hermes-` | Comma-separated container name prefixes that bypass label check |
| `INJECT_LABEL` | First value from `ALLOWED_LABELS` | Label auto-injected on `POST /containers/create` |
| `LISTEN_HOST` | `0.0.0.0` | Bind address |
| `LISTEN_PORT` | `2375` | Bind port |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Docker daemon socket path |

**Backward compatibility:** Defaults match current hardcoded values, so existing hermes-agent deployment works without changes.

### 2. docker-compose.yml additions

New `docker-guard` service in hw-rnd-ai-crew:

```yaml
docker-guard:
  build: ./docker-guard
  container_name: hw-docker-guard
  restart: unless-stopped
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
  environment:
    ALLOWED_LABELS: "docker-guard.allow"
    ALLOWED_PREFIXES: ""
    LISTEN_PORT: "2375"
  networks:
    - local-ai-internal
```

`hermes-gateway` adds:
- `depends_on: [docker-guard]`
- Already on `local-ai-internal` network (docker-guard accessible at `docker-guard:2375`)

### 3. Per-agent DOCKER_HOST via adapter_config

**Orchestrator change** (`orchestrator.py`):

Read `adapter_config.docker.enabled` (boolean) from agent DB record. When `true`, add `DOCKER_HOST=tcp://docker-guard:2375` to the supervisor program environment for that agent's gateway process.

**UI path:** Agent detail page → adapter config → `docker.enabled: true`

**Supervisor config example (with docker):**
```
[program:gateway-abc123456789]
command=hermes -p <agent_id> gateway run
environment=...,DOCKER_HOST="tcp://docker-guard:2375"
```

**Supervisor config example (without docker):**
```
[program:gateway-def456789012]
command=hermes -p <agent_id> gateway run
environment=...
```
No DOCKER_HOST — agent cannot reach Docker.

### 4. Target container labels

`suckless-shopping/docker-compose.yaml` — add `docker-guard.allow: "true"` label to all three services:

- `grocy`
- `grocy-shopping-agent`
- `mail-receipts`

This is the only change needed in suckless-shopping. Future services just add the same label to gain agent access.

### 5. Agent instructions

Agents with Docker access get an additional section in SOUL.md (via `_build_soul_md` or per-agent instructions):

```markdown
## Docker Access
Контейнеры suckless-shopping доступны через Docker CLI:
- `docker restart <container>` — перезапуск
- `docker logs <container> [-f]` — логи
- `docker stop/start <container>` — управление жизненным циклом
- `docker exec <container> <cmd>` — выполнение команд внутри контейнера
Доступные контейнеры: grocy, grocy-shopping-agent, mail-receipts
```

## Security Model

- **docker-guard** enforces: only containers with `docker-guard.allow=true` label can be mutated (stop/start/restart/exec/rm)
- **Read-only** (GET/HEAD/OPTIONS) passes through unrestricted
- **Network deletion** and **prune** blocked entirely
- **Container creation** auto-injects the allow label
- Agents without `DOCKER_HOST` env var have zero Docker access (not even read)
- Docker socket mounted read-only in docker-guard container

## Deployment Steps

1. Add docker-guard submodule: `git submodule add https://github.com/VladimirHulagov/docker-guard`
2. Modify `guard.py` to read config from env vars
3. Add docker-guard service to `docker-compose.yml`
4. Add labels to `suckless-shopping/docker-compose.yaml`
5. Modify orchestrator to read `adapter_config.docker.enabled` and set `DOCKER_HOST`
6. Update agent instructions (SOUL.md / AGENTS.md)
7. Rebuild and restart: `docker compose up -d --build --force-recreate`

## Testing

1. Verify docker-guard starts and accepts connections: `docker exec hermes-gateway docker -H tcp://docker-guard:2375 ps`
2. Verify label enforcement: agent can restart `grocy` but not `paperclip-server`
3. Verify per-agent control: agent without `docker.enabled=true` cannot reach Docker
