# docker-guard Deploy Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend docker-guard to act as a deploy node — agents can create compose files, deploy new services, and configure Traefik routing through `docker exec hw-docker-guard`.

**Architecture:** docker-guard container gets Docker CLI + compose plugin, rw Docker socket, and rw mount to `/mnt/services/agent-deploy/` sandbox. Agents use `docker exec` for all deploy operations. Traefik picks up new containers automatically via labels on shared `traefik-public` network.

**Tech Stack:** Docker CLI, docker compose plugin, Python 3 (guard.py), Traefik labels, Alpine Linux

---

### Task 1: Update docker-guard Dockerfile

**Files:**
- Modify: `docker-guard/Dockerfile`

- [ ] **Step 1: Rewrite Dockerfile**

Replace `python:3.12-alpine` with `docker:cli` base + python3 + compose plugin:

```dockerfile
FROM docker:cli

RUN apk add --no-cache python3 docker-cli-compose

COPY guard.py /guard.py

EXPOSE 2375
CMD ["python3", "/guard.py"]
```

- [ ] **Step 2: Commit**

```bash
git add docker-guard/Dockerfile
git commit -m "feat: extend docker-guard with Docker CLI + compose plugin"
```

---

### Task 2: Update docker-compose.yml for deploy node

**Files:**
- Modify: `docker-compose.yml` (docker-guard service, lines 16-27)

- [ ] **Step 1: Update docker-guard service definition**

Change volumes (socket ro→rw, add agent-deploy rw mount) and add env var:

```yaml
  docker-guard:
    build: ./docker-guard
    container_name: hw-docker-guard
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /mnt/services/agent-deploy:/mnt/services/agent-deploy:rw
    environment:
      ALLOWED_LABELS: "docker-guard.allow"
      ALLOWED_PREFIXES: ""
      LISTEN_PORT: "2375"
      AGENT_DEPLOY_DIR: "/mnt/services/agent-deploy"
    networks:
      - local-ai-internal
```

Key changes:
- `/var/run/docker.sock` — removed `:ro`, now rw (needed for `docker compose up`)
- Added `/mnt/services/agent-deploy:/mnt/services/agent-deploy:rw` — sandbox for agent deploy operations
- Added `AGENT_DEPLOY_DIR` env var — for future path validation in guard.py

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: docker-guard deploy node — rw socket + agent-deploy sandbox"
```

---

### Task 3: Rebuild and verify docker-guard

- [ ] **Step 1: Rebuild docker-guard image**

```bash
docker compose build docker-guard
```

- [ ] **Step 2: Recreate container**

```bash
docker compose up -d --force-recreate docker-guard
```

- [ ] **Step 3: Verify container has Docker CLI + compose**

```bash
docker exec hw-docker-guard docker --version
docker exec hw-docker-guard docker compose version
```

Expected: both commands return version strings.

- [ ] **Step 4: Verify agent-deploy mount**

```bash
docker exec hw-docker-guard ls -la /mnt/services/agent-deploy/
docker exec hw-docker-guard sh -c 'echo test > /mnt/services/agent-deploy/test-write.txt && cat /mnt/services/agent-deploy/test-write.txt && rm /mnt/services/agent-deploy/test-write.txt'
```

Expected: directory listing succeeds, write/read/delete test passes.

- [ ] **Step 5: Verify guard.py still works**

```bash
docker logs --tail 20 hw-docker-guard
```

Expected: `docker-guard listening on ...` log line, no errors.

- [ ] **Step 6: Verify proxy still works**

```bash
docker exec hw-docker-guard docker ps --filter "label=docker-guard.allow" --format "table {{.Names}}\t{{.Status}}"
```

Expected: list of allowed containers (hw-docker-guard itself plus any with the label).

---

### Task 4: Update SKILL.md — deploy capabilities

**Files:**
- Modify: `hermes-gateway/skills/devops/docker-management/SKILL.md`

- [ ] **Step 1: Add new sections to SKILL.md**

Append after the existing "## Ограничения" section. The full new content to append:

```markdown

## Деплой новых сервисов

Агент может развёртывать новые сервисы через `docker exec hw-docker-guard`. Все файлы создаются в песочнице `/mnt/services/agent-deploy/<service-name>/`.

### Пошаговый процесс

1. **Создать директорию:**
```bash
docker exec hw-docker-guard mkdir -p /mnt/services/agent-deploy/<name>
```

2. **Написать docker-compose.yml:**
```bash
docker exec hw-docker-guard sh -c 'cat > /mnt/services/agent-deploy/<name>/docker-compose.yml << EOF
services:
  <name>:
    image: <image>:<tag>
    restart: unless-stopped
    labels:
      docker-guard.allow: "true"
      traefik.enable: "true"
      traefik.http.routers.<name>.rule: "Host(\`<subdomain>.<domain>\`)"
      traefik.http.routers.<name>.entrypoints: websecure
      traefik.http.routers.<name>.tls: "true"
      traefik.http.services.<name>.loadbalancer.server.port: "<port>"
    networks:
      - traefik-public
networks:
  traefik-public:
    external: true
EOF'
```

3. **Запустить сервис:**
```bash
docker exec hw-docker-guard docker compose -f /mnt/services/agent-deploy/<name>/docker-compose.yml up -d
```

4. **Проверить:**
```bash
docker exec hw-docker-guard docker ps --filter "label=docker-guard.allow"
docker exec hw-docker-guard docker logs <container-name>
```

### Важные правила

- **Создавай сервисы ТОЛЬКО в `/mnt/services/agent-deploy/`** — не трогай другие директории
- **Всегда добавляй метку `docker-guard.allow: "true"`** — без неё контейнер неуправляем
- **Проверяй что DNS-запись покрывается wildcard-сертификатом** — `*.suckless.space` и `*.collaborationism.tech` уже настроены
- **Проверяй что порт не занят** — перед деплоем убедись что целевой порт свободен

## Настройка Traefik

Traefik автоматически маршрутизирует трафик к контейнерам с правильными labels. Wildcard-сертификаты уже настроены для `*.suckless.space` и `*.collaborationism.tech`.

### Обязательные labels для публичного доступа

```yaml
labels:
  traefik.enable: "true"
  traefik.http.routers.<router-name>.rule: "Host(\`<subdomain>.<domain>\`)"
  traefik.http.routers.<router-name>.entrypoints: websecure
  traefik.http.routers.<router-name>.tls: "true"
  traefik.http.services.<router-name>.loadbalancer.server.port: "<internal-port>"
```

### Требования к сети

Контейнер должен быть в сети `traefik-public`:

```yaml
networks:
  traefik-public:
    external: true
```

### Проверка маршрутизации

```bash
docker exec hw-docker-guard docker inspect <container> --format '{{json .NetworkSettings.Networks}}' | python3 -m json.tool
curl -sI https://<subdomain>.<domain>
```

## Управление конфигурациями

### Создать .env файл

```bash
docker exec hw-docker-guard sh -c 'cat > /mnt/services/agent-deploy/<name>/.env << EOF
KEY1=value1
KEY2=value2
EOF'
```

### Обновить .env и перезапустить

```bash
docker exec hw-docker-guard sh -c 'echo "NEW_KEY=value" >> /mnt/services/agent-deploy/<name>/.env'
docker exec hw-docker-guard docker compose -f /mnt/services/agent-deploy/<name>/docker-compose.yml up -d
```

### Редактировать config.json внутри volume

Если конфиг в named volume, используем временный контейнер:

```bash
docker exec hw-docker-guard docker run --rm -v <volume-name>:/data alpine sh -c 'echo "{\"key\": \"value\"}" > /data/config.json'
```

Если конфиг в bind mount внутри agent-deploy:

```bash
docker exec hw-docker-guard sh -c 'echo "{\"key\": \"value\"}" > /mnt/services/agent-deploy/<name>/config.json'
```

## Удаление сервиса

```bash
docker exec hw-docker-guard docker compose -f /mnt/services/agent-deploy/<name>/docker-compose.yml down
docker exec hw-docker-guard rm -rf /mnt/services/agent-deploy/<name>
```
```

- [ ] **Step 2: Commit**

```bash
git add hermes-gateway/skills/devops/docker-management/SKILL.md
git commit -m "feat: add deploy, Traefik, and config sections to docker-management SKILL.md"
```

---

### Task 5: End-to-end smoke test — deploy a test container

- [ ] **Step 1: Deploy a test whoami container through the agent flow**

```bash
docker exec hw-docker-guard mkdir -p /mnt/services/agent-deploy/test-whoami
docker exec hw-docker-guard sh -c 'cat > /mnt/services/agent-deploy/test-whoami/docker-compose.yml << EOF
services:
  test-whoami:
    image: traefik/whoami
    labels:
      docker-guard.allow: "true"
      traefik.enable: "true"
      traefik.http.routers.test-whoami.rule: "Host(\`test-whoami.suckless.space\`)"
      traefik.http.routers.test-whoami.entrypoints: websecure
      traefik.http.routers.test-whoami.tls: "true"
      traefik.http.services.test-whoami.loadbalancer.server.port: "80"
    networks:
      - traefik-public
networks:
  traefik-public:
    external: true
EOF'
docker exec hw-docker-guard docker compose -f /mnt/services/agent-deploy/test-whoami/docker-compose.yml up -d
```

- [ ] **Step 2: Verify container is running**

```bash
docker exec hw-docker-guard docker ps --filter "name=test-whoami" --format "{{.Names}} {{.Status}}"
```

Expected: `test-whoami Up ...`

- [ ] **Step 3: Verify Traefik routing**

```bash
curl -sI https://test-whoami.suckless.space
```

Expected: HTTP 200 response.

- [ ] **Step 4: Verify container is visible through guard proxy**

```bash
docker exec hw-docker-guard docker ps --filter "label=docker-guard.allow" --format "table {{.Names}}\t{{.Status}}"
```

Expected: `test-whoami` appears in the list.

- [ ] **Step 5: Clean up test container**

```bash
docker exec hw-docker-guard docker compose -f /mnt/services/agent-deploy/test-whoami/docker-compose.yml down
docker exec hw-docker-guard rm -rf /mnt/services/agent-deploy/test-whoami
```
