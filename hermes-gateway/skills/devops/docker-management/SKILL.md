---
name: docker-management
description: Управление Docker-контейнерами через docker-guard прокси — перезапуск, логи, инспекция, отладка сервисов.
version: 1.0.0
metadata:
  hermes:
    tags: [docker, containers, devops, infrastructure, debugging]
    category: devops
    requires_toolsets: [terminal]
---

# Docker Management (docker-guard)

Управление Docker-контейнерами через защищённый прокси docker-guard. Прокси ограничивает операции по меткам контейнеров.

## Модель безопасности

| Операция | Доступ |
|----------|--------|
| Чтение (`docker ps`, `docker logs`, `docker inspect`) | Без ограничений |
| Перезапуск, stop/start, rm | Только контейнеры с меткой `docker-guard.allow=true` |
| Создание контейнеров (`docker run/create`) | Метка `docker-guard.allow=true` инжектится автоматически |
| Exec (`docker exec`) | Только для разрешённых контейнеров |
| Prune, network delete | **Заблокировано** |

Если операция заблокирована — прокси вернёт 403. Это нормально: контейнер не имеет нужной метки.

## Доступные контейнеры

Узнать разрешённые контейнеры:
```bash
docker ps --filter "label=docker-guard.allow" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

## Типичные операции

### Статус и инспекция

```bash
docker ps -a                                    # все контейнеры
docker ps --format "table {{.Names}}\t{{.Status}}"  # компактно
docker inspect <container>                       # полная информация (JSON)
docker stats --no-stream                         # ресурсы
```

### Логи

```bash
docker logs --tail 100 <container>               # последние 100 строк
docker logs --since 2h <container>               # за последние 2 часа
docker logs --tail 50 -f <container>             # следить (Ctrl+C для остановки)
docker logs --since 30m <container> 2>&1 | tail -50  # ошибки за 30 мин
```

### Управление жизненным циклом

```bash
docker restart <container>                       # перезапуск
docker stop <container>                          # остановка
docker start <container>                         # запуск остановленного
```

### Exec (выполнение команд)

```bash
docker exec <container> ls /app                  # список файлов
docker exec <container> cat /var/log/app.log     # прочитать лог внутри
docker exec <container> sh -c "ps aux | grep node"  # составная команда
```

**Важно:** интерактивный exec (`docker exec -it`) не поддерживается через прокси. Используй неинтерактивные команды.

## Рабочие процессы

### Сервис упал / перезапустить

```bash
docker ps -a --filter "name=<container>" --format "{{.Status}}"
docker logs --tail 30 <container>
docker restart <container>
docker ps --filter "name=<container>" --format "{{.Status}}"
```

### Исследовать проблему

```bash
docker inspect <container> --format '{{.State.Status}} {{.State.ExitCode}}'
docker logs --since 1h <container> 2>&1 | grep -iE "error|fail|exception"
docker exec <container> cat /etc/config.conf
```

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
- **Проверяй что DNS резолвится** — `*.collaborationism.tech` имеет wildcard A-запись (автоматически резолвится). `*.suckless.space` wildcard DNS НЕТ — нужна отдельная A-запись (попроси CEO добавить)
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
docker exec hw-docker-guard docker inspect <container> --format '{{json .NetworkSettings.Networks}}'
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

### Редактировать config.json

Если конфиг в bind mount внутри agent-deploy:

```bash
docker exec hw-docker-guard sh -c 'echo "{\"key\": \"value\"}" > /mnt/services/agent-deploy/<name>/config.json'
```

Если конфиг в named volume — используем временный контейнер:

```bash
docker exec hw-docker-guard docker run --rm -v <volume-name>:/data alpine sh -c 'echo "{\"key\": \"value\"}" > /data/config.json'
```

## Удаление сервиса

```bash
docker exec hw-docker-guard docker compose -f /mnt/services/agent-deploy/<name>/docker-compose.yml down
docker exec hw-docker-guard rm -rf /mnt/services/agent-deploy/<name>
```

## Ограничения

- **Нет interactive exec** — прокси не поддерживает TTY-сессии
- **Нет prune** — `docker system prune`, `docker container prune` заблокированы
- **Нет network delete** — удаление сетей заблокировано
- **403 на мутации** — контейнер не имеет метки `docker-guard.allow=true`
