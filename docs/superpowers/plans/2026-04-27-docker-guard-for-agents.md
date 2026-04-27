# Docker-Guard for Paperclip Agents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add docker-guard as a label-enforcing Docker API proxy to hw-rnd-ai-crew, giving per-agent Docker access to manage external services like suckless-shopping.

**Architecture:** docker-guard sits between hermes-gateway and the Docker socket, enforcing that only containers with the `docker-guard.allow=true` label can be mutated. The orchestrator reads `adapter_config.docker.enabled` per agent and conditionally sets `DOCKER_HOST` in the supervisor config. Target containers (suckless-shopping) are labelled to grant access.

**Tech Stack:** Python (docker-guard), Docker Compose, Supervisor (orchestrator), YAML config

---

### Task 1: Add docker-guard submodule

**Files:**
- Create: `docker-guard/` (git submodule)
- Modify: `.gitmodules`

- [ ] **Step 1: Add submodule**

```bash
git submodule add https://github.com/VladimirHulagov/docker-guard.git docker-guard
```

- [ ] **Step 2: Verify submodule initialized**

```bash
ls docker-guard/guard.py docker-guard/Dockerfile
```

Expected: both files exist.

- [ ] **Step 3: Commit**

```bash
git add docker-guard .gitmodules
git commit -m "feat: add docker-guard submodule"
```

---

### Task 2: Make docker-guard configurable via env vars

**Files:**
- Modify: `docker-guard/guard.py`

This change makes docker-guard read configuration from environment variables instead of hardcoded constants. Defaults match the original values for backward compatibility with the hermes-agent deployment.

- [ ] **Step 1: Edit guard.py — replace constants with env var reads**

Replace lines 36-41 (the constants block):

```python
DOCKER_SOCKET = "/var/run/docker.sock"
TEST_LABEL = "hermes-test"
ALLOWED_PREFIXES = ("hermes-",)
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 2375
```

With:

```python
DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
_ALLOWED_LABELS_RAW = os.environ.get("ALLOWED_LABELS", "hermes-test")
ALLOWED_LABELS = [l.strip() for l in _ALLOWED_LABELS_RAW.split(",") if l.strip()]
ALLOWED_PREFIXES = tuple(p.strip() for p in os.environ.get("ALLOWED_PREFIXES", "hermes-").split(",") if p.strip())
INJECT_LABEL = os.environ.get("INJECT_LABEL", ALLOWED_LABELS[0] if ALLOWED_LABELS else "hermes-test")
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "2375"))
```

- [ ] **Step 2: Edit guard.py — update `_is_test_container` to check multiple labels**

Replace the `_is_test_container` function (lines 79-86):

```python
async def _is_test_container(ref: str) -> bool:
    """Return True if *ref* (name or ID) refers to a test container."""
    if any(ref.startswith(p) for p in ALLOWED_PREFIXES):
        return True
    info = await _docker_get(f"/containers/{ref}/json")
    if not info:
        return False
    labels = info.get("Config", {}).get("Labels", {})
    return any(labels.get(label) == "true" for label in ALLOWED_LABELS)
```

- [ ] **Step 3: Edit guard.py — update error messages in `_handle`**

In the `_handle` method, update the 403 error messages to be generic. Change all occurrences of:

```python
                    f"container '{cid}' is not a hermes-test container".encode(),
```

To:

```python
                    f"container '{cid}' is not an allowed container".encode(),
```

And change:

```python
                    _send_response(client_w, 403, b"container is not a hermes-test container")
```

To:

```python
                    _send_response(client_w, 403, b"container is not an allowed container")
```

- [ ] **Step 4: Edit guard.py — update `_inject_label` to use INJECT_LABEL**

Replace the `_inject_label` method (lines 214-221):

```python
    @staticmethod
    def _inject_label(body: bytes) -> bytes:
        try:
            config = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return body
        config.setdefault("Labels", {})[INJECT_LABEL] = "true"
        return json.dumps(config).encode()
```

- [ ] **Step 5: Edit guard.py — update startup log to show config**

Replace the `serve` method (lines 133-138):

```python
    async def serve(self):
        server = await asyncio.start_server(self._on_client, LISTEN_HOST, LISTEN_PORT)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
        LOG.info("docker-guard listening on %s", addrs)
        LOG.info("Config: labels=%s prefixes=%s inject=%s socket=%s",
                 ALLOWED_LABELS, ALLOWED_PREFIXES, INJECT_LABEL, DOCKER_SOCKET)
        async with server:
            await server.serve_forever()
```

- [ ] **Step 6: Commit submodule changes**

```bash
cd docker-guard && git add guard.py && git commit -m "feat: make all config params env-var configurable" && cd ..
```

Note: If you don't have push access to the docker-guard repo, commit locally in the submodule. The submodule commit reference will be tracked by the parent repo.

- [ ] **Step 7: Stage submodule pointer in parent repo**

```bash
git add docker-guard
git commit -m "feat: update docker-guard submodule with env var config"
```

---

### Task 3: Add docker-guard service to docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add docker-guard service**

Insert after the `qdrant` service block (after line 14, before `ollama`):

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

- [ ] **Step 2: Add docker-guard dependency to hermes-gateway**

In the `hermes-gateway` service, change the `depends_on` block (line 92-94) from:

```yaml
    depends_on:
      paperclip-server:
        condition: service_started
```

To:

```yaml
    depends_on:
      paperclip-server:
        condition: service_started
      docker-guard:
        condition: service_started
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add docker-guard service to compose stack"
```

---

### Task 4: Label suckless-shopping containers

**Files:**
- Modify: `/mnt/services/suckless-shopping/docker-compose.yaml`

- [ ] **Step 1: Add label to grocy service**

In the `grocy` service, add a label to the existing `labels:` block (after line 25):

```yaml
      docker-guard.allow: "true"
```

The grocy labels section should become:

```yaml
    labels:
      traefik.enable: true
      traefik.http.routers.grocy.rule: Host(`${SUB_DOMAIN}.${SERVER_DOMAIN}`)
      traefik.http.routers.grocy.entrypoints: websecure
      traefik.http.routers.grocy.tls: true
      traefik.http.services.grocy.loadbalancer.server.port: 80
      homepage.group: Home
      homepage.name: Grocy
      homepage.icon: grocy
      homepage.href: https://${SUB_DOMAIN}.${SERVER_DOMAIN}
      homepage.description: Groceries & household management
      docker-guard.allow: "true"
```

- [ ] **Step 2: Add label to grocy-shopping-agent service**

Add a `labels:` block to the `grocy-shopping-agent` service (it currently has none). Insert before `networks:` (before line 49):

```yaml
    labels:
      docker-guard.allow: "true"
```

- [ ] **Step 3: Add label to mail-receipts service**

Add a `labels:` block to the `mail-receipts` service. Insert before `volumes:` (before line 77):

```yaml
    labels:
      docker-guard.allow: "true"
```

- [ ] **Step 4: Restart suckless-shopping to apply labels**

```bash
cd /mnt/services/suckless-shopping && docker compose up -d --force-recreate
```

- [ ] **Step 5: Verify labels applied**

```bash
docker inspect grocy --format '{{.Config.Labels}}' | grep docker-guard
docker inspect grocy-shopping-agent --format '{{.Config.Labels}}' | grep docker-guard
docker inspect mail-receipts --format '{{.Config.Labels}}' | grep docker-guard
```

Expected: all three show `docker-guard.allow:true`.

Note: suckless-shopping is a separate repo, commit there separately.

---

### Task 5: Add per-agent DOCKER_HOST to orchestrator

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Read docker config from adapter_config**

In `provision_agent` method, after the `adapter_config` extraction (around line 360-361), add:

```python
        agent_docker = adapter_config.get("docker", {}) or {}
        enable_docker = agent_docker.get("enabled", False)
```

- [ ] **Step 2: Add DOCKER_HOST to supervisor environment**

In `provision_agent`, modify the `program_conf` string (lines 432-445). The current `environment=` line is a single long string. Add the DOCKER_HOST variable conditionally.

Replace the `program_conf = (...)` block with:

```python
        base_env = (
            f"HERMES_HOME=\"{profile_dir}\""
            f",PAPERCLIP_RUN_API_KEY=\"{agent_key}\""
            f",TELEGRAM_BOT_TOKEN=\"{agent_telegram.get('botToken', '') if enable_telegram else ''}\""
            f",TELEGRAM_CHAT_ID=\"{agent_telegram.get('chatId', '') if enable_telegram else ''}\""
            f",TELEGRAM_HOME_CHANNEL=\"{agent_telegram.get('chatId', '') if enable_telegram else ''}\""
            f",TELEGRAM_CLARIFY_TIMEOUT=\"{agent_telegram.get('defaultTimeout', 600) if enable_telegram else '600'}\""
            f",TELEGRAM_ALLOWED_USERS=\"{agent_telegram.get('allowedUsers', '') if enable_telegram else ''}\""
            f",TELEGRAM_REQUIRE_MENTION=\"true\""
        )
        if enable_docker:
            base_env += ",DOCKER_HOST=\"tcp://docker-guard:2375\""

        program_conf = (
            f"[program:{proc_name}]\n"
            f"command={command}\n"
            f"directory=/\n"
            f"environment={base_env}\n"
            f"autostart=true\n"
            f"autorestart=true\n"
            f"stdout_logfile=/dev/fd/1\n"
            f"stdout_logfile_maxbytes=0\n"
            f"redirect_stderr=true\n"
            f"priority=10\n"
            f"startsecs=5\n"
            f"startretries=3\n"
        )
```

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "feat: per-agent DOCKER_HOST via adapter_config.docker.enabled"
```

---

### Task 6: Update _build_soul_md for Docker-aware agents

**Files:**
- Modify: `hermes-gateway/orchestrator/orchestrator.py`

- [ ] **Step 1: Add Docker section to _build_soul_md**

In `_build_soul_md` function (around line 229), add a `docker_guidance` parameter and conditionally include Docker instructions.

Change the function signature from:

```python
def _build_soul_md(role: str, name: str) -> str:
```

To:

```python
def _build_soul_md(role: str, name: str, enable_docker: bool = False) -> str:
```

Add the docker guidance block after `paperclip_guidance`:

```python
    docker_guidance = (
        "\n## Docker Access\n"
        "У тебя есть доступ к Docker CLI для управления контейнерами.\n"
        "Команды:\n"
        "- `docker ps` — список запущенных контейнеров\n"
        "- `docker restart <container>` — перезапуск\n"
        "- `docker logs <container> [-f]` — логи\n"
        "- `docker stop/start <container>` — управление жизненным циклом\n"
        "- `docker exec <container> <cmd>` — выполнение команд\n"
    )
```

Then update both return paths to include `docker_guidance` when `enable_docker` is true. The `ceo`/`cto` return becomes:

```python
    if role in ("ceo", "cto"):
        return (
            f"Ты — {name}, руководящий агент в системе управления задачами Paperclip.\n"
            "Твоя задача — стратегия, приоритизация, координация и делегирование.\n"
            "Все документы и тексты создавай на русском языке.\n"
            + outline_guidance
            + paperclip_guidance
            + (docker_guidance if enable_docker else "")
        )
```

And the general role return:

```python
    return (
        f"Ты — {name}, рабочий агент в системе управления задачами Paperclip.\n"
        "Твоя задача — выполнять задания: исследование, кодирование, тестирование, документирование, анализ.\n"
        "Все документы и тексты создавай на русском языке.\n"
        + outline_guidance
        + paperclip_guidance
        + (docker_guidance if enable_docker else "")
    )
```

- [ ] **Step 2: Pass enable_docker to _build_soul_md in provision_agent**

In `provision_agent`, update the line that calls `_build_soul_md` (around line 404):

```python
        soul_content = _read_paperclip_instructions(agent_id, company_id) or _build_soul_md(role, name)
```

To:

```python
        soul_content = _read_paperclip_instructions(agent_id, company_id) or _build_soul_md(role, name, enable_docker)
```

This requires that `enable_docker` is defined before this line. It already is from Task 5 Step 1.

- [ ] **Step 3: Commit**

```bash
git add hermes-gateway/orchestrator/orchestrator.py
git commit -m "feat: add Docker guidance to agent SOUL.md when docker enabled"
```

---

### Task 7: Build, deploy, and verify

- [ ] **Step 1: Build docker-guard image**

```bash
docker build -t hw-docker-guard ./docker-guard
```

- [ ] **Step 2: Rebuild hermes-gateway with orchestrator changes**

```bash
docker compose up -d --build --force-recreate docker-guard hermes-gateway
```

- [ ] **Step 3: Verify docker-guard is running**

```bash
docker ps --filter name=hw-docker-guard
docker logs hw-docker-guard 2>&1 | head -5
```

Expected: log shows `docker-guard listening on ...` and `Config: labels=['docker-guard.allow'] prefixes=() inject=docker-guard.allow`.

- [ ] **Step 4: Verify connectivity from hermes-gateway**

```bash
docker exec hermes-gateway python -c "
import urllib.request
try:
    r = urllib.request.urlopen('http://docker-guard:2375/_ping')
    print('OK:', r.read().decode())
except Exception as e:
    print('FAIL:', e)
"
```

Expected: `OK: OK`

- [ ] **Step 5: Verify label enforcement — allowed container**

```bash
docker exec hermes-gateway curl -s -X POST http://docker-guard:2375/containers/grocy/restart
```

Expected: 204 No Content (grocy restarts).

- [ ] **Step 6: Verify label enforcement — blocked container**

```bash
docker exec hermes-gateway curl -s -X POST http://docker-guard:2375/containers/paperclip-server/restart
```

Expected: 403 Forbidden — `container 'paperclip-server' is not an allowed container`.

- [ ] **Step 7: Verify per-agent DOCKER_HOST**

Check that an agent with `adapter_config.docker.enabled=true` has DOCKER_HOST in its supervisor process environment:

```bash
docker exec hermes-gateway supervisorctl status
# Find a gateway process, get its PID
docker exec hermes-gateway cat /proc/<PID>/environ | tr '\0' '\n' | grep DOCKER_HOST
```

Expected: `DOCKER_HOST=tcp://docker-guard:2375` for enabled agents, no DOCKER_HOST for others.

- [ ] **Step 8: Final commit**

```bash
git add -A
git commit -m "feat: docker-guard integration complete — per-agent Docker access"
```
