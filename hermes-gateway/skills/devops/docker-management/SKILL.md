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

## Ограничения

- **Нет interactive exec** — прокси не поддерживает TTY-сессии
- **Нет prune** — `docker system prune`, `docker container prune` заблокированы
- **Нет network delete** — удаление сетей заблокировано
- **403 на мутации** — контейнер не имеет метки `docker-guard.allow=true`
