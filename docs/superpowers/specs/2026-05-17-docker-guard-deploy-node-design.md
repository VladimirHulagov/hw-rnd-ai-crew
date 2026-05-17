# docker-guard как deploy-узел для агентов

## Проблема

Агенты Hermes могут управлять существующими контейнерами через docker-guard, но не могут деплоить новые сервисы. Для этого нужны: создание compose-файлов, запуск `docker compose up`, настройка Traefik routing.

Конкретный запрос: агент shopping хочет задеплоить PostHog на `analytics.suckless.space`.

## Решение

Расширить docker-guard до полноценного deploy-узла. Агент использует `docker exec hw-docker-guard` для создания compose-файлов, запуска сервисов и настройки Traefik.

## Архитектура

### Песочница agent-deploy

```
/mnt/services/
├── traefik/              ← защищено (вне agent-deploy)
├── hw-rnd-ai-crew/       ← защищено
├── nextcloud/            ← защищено
├── suckless-shopping/    ← защищено (уже смонтирован в hermes-gateway)
└── agent-deploy/         ← workspace агента (rw)
    └── <service-name>/   ← каждый сервис в своей папке
        ├── docker-compose.yml
        ├── .env
        └── ...
```

- Агент создаёт сервисы **только** внутри `/mnt/services/agent-deploy/`
- guard.py валидирует пути при `docker exec` — операции вне `agent-deploy/` запрещены
- Продвижение сервиса на уровень выше (`mv /mnt/services/agent-deploy/posthog /mnt/services/posthog`) — ручная операция человека

### Изменения в Dockerfile

```dockerfile
FROM docker:cli
RUN apk add --no-cache python3 py3-pip docker-cli-compose
COPY guard.py /guard.py
EXPOSE 2375
CMD ["python3", "/guard.py"]
```

Заменяем `python:3.12-alpine` на `docker:cli` — получаем Docker CLI + compose plugin. Python устанавливаем поверх.

### Изменения в docker-compose.yml

```yaml
docker-guard:
  build: ./docker-guard
  container_name: hw-docker-guard
  restart: unless-stopped
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock      # rw! (было ro)
    - /mnt/services/agent-deploy:/mnt/services/agent-deploy:rw  # NEW: sandbox
  environment:
    ALLOWED_LABELS: "docker-guard.allow"
    ALLOWED_PREFIXES: ""
    LISTEN_PORT: "2375"
    AGENT_DEPLOY_DIR: "/mnt/services/agent-deploy"   # NEW: path validation
  networks:
    - local-ai-internal
```

Docker socket меняется с `ro` на `rw` — нужно для `docker compose up` (создаёт контейнеры). Но proxy-логика guard.py по-прежнему контролирует мутации через label-check.

### Изменения в guard.py

Добавляется валидация путей для exec-команд в docker-guard:

**Принцип:** агент может `docker exec` в любой разрешённый контейнер. Но `docker exec hw-docker-guard` (в самого себя) требует проверки — агент не должен выходить за пределы `AGENT_DEPLOY_DIR`.

Реализация: при `POST /containers/{id}/exec` проверяем:
1. Если целевой контейнер — не `hw-docker-guard` — стандартная label-проверка
2. Если `hw-docker-guard` — пропускаем exec (создаём exec instance), но при `POST /exec/{id}/start` перехватываем stdin/stdout и валидируем команду

Альтернатива (проще): не валидировать внутри guard.py, а положиться на SKILL.md инструкции + аудит через логи docker-guard. Агент доверенный (`enable_docker=true`), а человек может продвигать сервисы вручную.

**Рекомендация:** начать без path-validation в guard.py. Добавить только если появится прецедент. Агенты следуют SKILL.md, человек видит все exec-команды в логах.

### Traefik интеграция

Wildcard-сертификаты `*.suckless.space` и `*.collaborationism.tech` уже настроены. Контейнер автоматически появляется в Traefik если:

1. Контейнер в сети `traefik-public` (external network)
2. Имеет labels:
   ```yaml
   labels:
     traefik.enable: "true"
     traefik.http.routers.<name>.rule: "Host(`analytics.suckless.space`)"
     traefik.http.routers.<name>.entrypoints: websecure
     traefik.http.routers.<name>.tls: "true"
     traefik.http.services.<name>.loadbalancer.server.port: "8000"
   ```

Шаблон compose-файла для агента:
```yaml
services:
  posthog:
    image: posthog/posthog:latest
    labels:
      docker-guard.allow: "true"
      traefik.enable: "true"
      traefik.http.routers.posthog.rule: "Host(`analytics.suckless.space`)"
      traefik.http.routers.posthog.entrypoints: websecure
      traefik.http.routers.posthog.tls: "true"
      traefik.http.services.posthog.loadbalancer.server.port: "8000"
    networks:
      - traefik-public
      - default

networks:
  traefik-public:
    external: true
```

### Безопасность

| Аспект | Защита |
|--------|--------|
| Существующие сервисы | `/mnt/services/agent-deploy/` — только rw mount. Всё остальное на хосте недоступно |
| Мутации чужих контейнеров | Label-check в guard.py — без `docker-guard.allow=true` мутации запрещены |
| Prune | Заблокировано в guard.py |
| Network delete | Заблокировано для сетей без inject label |
| Создание контейнеров | `docker-guard.allow=true` инжектится автоматически |
| Docker socket rw | Guard.py контролирует мутации. `docker compose up` через exec в docker-guard — proxy path для API-запросов агента остаётся с label-check |

### Рабочий процесс агента

1. **Создать директорию:**
   ```bash
   docker exec hw-docker-guard mkdir -p /mnt/services/agent-deploy/posthog
   ```

2. **Написать compose-файл:**
   ```bash
   docker exec hw-docker-guard sh -c 'cat > /mnt/services/agent-deploy/posthog/docker-compose.yml << EOF
   ... compose content ...
   EOF'
   ```

3. **Запустить сервис:**
   ```bash
   docker exec hw-docker-guard docker compose -f /mnt/services/agent-deploy/posthog/docker-compose.yml up -d
   ```

4. **Проверить:**
   ```bash
   docker ps --filter "label=docker-guard.allow"
   docker logs posthog
   curl -sI https://analytics.suckless.space
   ```

5. **Настроить конфиг приложения** (например, config.json):
   ```bash
   docker exec hw-docker-guard sh -c 'cat > /mnt/services/agent-deploy/posthog/config.json << EOF
   {"apiKey": "phx_...", "apiHost": "https://analytics.suckless.space"}
   EOF'
   ```

### Продвижение сервиса

Когда сервис стабилен, человек переносит его из песочницы:

```bash
# На хосте
mv /mnt/services/agent-deploy/posthog /mnt/services/posthog
cd /mnt/services/posthog
docker compose up -d   # перезапуск с новым путём
```

После продвижения агент теряет доступ (папка вне agent-deploy).

### Обновлённый SKILL.md

Текущий `hermes-gateway/skills/devops/docker-management/SKILL.md` расширяем секциями:

1. **«Деплой новых сервисов»** — пошаговая инструкция создания compose-файлов в `/mnt/services/agent-deploy/`
2. **«Настройка Traefik»** — шаблоны labels, сети, TLS
3. **«Управление конфигами»** — редактирование .env, config.json через `docker exec`
4. **«Продвижение сервисов»** — информационная секция (агент не может продвигать сам)

## Файлы для изменения

| Файл | Изменение |
|------|-----------|
| `docker-guard/Dockerfile` | `docker:cli` база + python3 + docker-cli-compose |
| `docker-compose.yml` | docker-guard volumes: socket rw, agent-deploy rw; env AGENT_DEPLOY_DIR |
| `hermes-gateway/skills/devops/docker-management/SKILL.md` | Новые секции: деплой, Traefik, конфиги |
| `docker-guard/guard.py` | Без изменений (v1). Path-validation — v2 если понадобится |

## Порядок внедрения

1. Создать `/mnt/services/agent-deploy/` на хосте
2. Пересобрать docker-guard (новый Dockerfile)
3. Обновить docker-compose.yml (маунты, env)
4. `docker compose up -d --force-recreate docker-guard`
5. Обновить SKILL.md
6. Добавить `enable_docker: true` агентам, которым нужен деплой
7. Тест: агент создаёт PostHog через docker exec
