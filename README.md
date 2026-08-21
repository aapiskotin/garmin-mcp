# Personal Garmin MCP

Локальный MCP-сервер для создания и планирования структурированных тренировок в
Garmin Connect через **неофициальные** Garmin endpoint'ы.

> Это экспериментальный личный проект. Garmin может изменить endpoint'ы, ограничить
> запросы или отозвать сессию без предупреждения. Не публикуйте сервер в интернет без
> отдельной аутентификации.

## Что реализовано

- проверка сохранённой Garmin-сессии;
- preview Garmin JSON без записи;
- создание тренировки и опциональное добавление в календарь;
- просмотр библиотеки тренировок и календаря;
- удаление тренировки и снятие тренировки с календаря;
- бег, велосипед, ходьба и хайкинг;
- временные, дистанционные и `lap button` шаги;
- интервальные повторы;
- цели по темпу, пульсу, мощности и каденсу;
- обязательный `confirm=true` для любых изменений.

## Установка

### Docker Compose — рекомендуемый вариант

Для локального запуска нужен Docker с поддержкой Compose. Соберите образ из корня
репозитория:

```bash
docker compose build
```

Сборка устанавливает Python-зависимости через `uv` строго из `uv.lock`.

Один раз выполните интерактивный вход. Пароль и MFA-код вводятся непосредственно
в контейнер и не сохраняются; OAuth-токены попадут в приватный named volume
`garmin-mcp_garmin_tokens`:

```bash
docker compose --profile login run --rm garmin-login
```

Запустите MCP:

```bash
docker compose up -d garmin-mcp
docker compose ps
```

Endpoint будет доступен по адресу `http://127.0.0.1:8000/mcp`. Чтобы использовать
другой локальный порт:

```bash
GARMIN_MCP_PORT=8765 docker compose up -d garmin-mcp
```

Логи и остановка:

```bash
docker compose logs -f garmin-mcp
docker compose down
```

`docker compose down` сохраняет Garmin-токены. Команда
`docker compose down -v` удалит volume вместе с токенами.

### Локальная установка без Docker

Требуются Python 3.12+ и `uv`.

```bash
cd /path/to/garmin-mcp
uv sync --extra dev
```

## Одноразовый логин

```bash
uv run garmin-mcp-login
```

Скрипт интерактивно спросит email, пароль и, при необходимости, MFA-код. Пароль
не сохраняется. OAuth-токены сохраняются в `.garmin-tokens/garmin_tokens.json` с
ограниченными правами. Папка уже добавлена в `.gitignore`.

Можно вынести токены в другое место:

```bash
export GARMIN_TOKEN_DIR=/safe/private/path/garmin-tokens
uv run garmin-mcp-login
```

## Локальный MCP через stdio

```bash
export GARMIN_TOKEN_DIR=/safe/private/path/garmin-tokens
uv run garmin-mcp
```

Пример конфигурации MCP-клиента:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/absolute/path/to/garmin-mcp/.venv/bin/garmin-mcp",
      "env": {
        "GARMIN_TOKEN_DIR": "/safe/private/path/garmin-tokens"
      }
    }
  }
}
```

## Streamable HTTP для ChatGPT

```bash
export GARMIN_MCP_TRANSPORT=streamable-http
export GARMIN_MCP_HOST=127.0.0.1
export GARMIN_MCP_PORT=8000
export GARMIN_TOKEN_DIR=/safe/private/path/garmin-tokens
uv run garmin-mcp
```

Endpoint: `http://127.0.0.1:8000/mcp`.

ChatGPT должен иметь возможность обратиться к endpoint по HTTPS. Для личного теста
можно использовать защищённый туннель. Не выставляйте этот MVP напрямую наружу:
в нём намеренно нет отдельного OAuth resource server для доступа к самому MCP.

## Пример аргумента для `preview_workout`

```json
{
  "workout": {
    "name": "6 x 800",
    "sport": "running",
    "description": "Контролируемые интервалы",
    "blocks": [
      {
        "steps": [
          {
            "step_type": "warmup",
            "duration_type": "time",
            "duration_value": 900
          }
        ]
      },
      {
        "repeat": 6,
        "steps": [
          {
            "step_type": "interval",
            "duration_type": "distance",
            "duration_value": 800,
            "target_type": "pace_seconds_per_km",
            "target_low": 250,
            "target_high": 260
          },
          {
            "step_type": "recovery",
            "duration_type": "time",
            "duration_value": 120
          }
        ]
      },
      {
        "steps": [
          {
            "step_type": "cooldown",
            "duration_type": "time",
            "duration_value": 600
          }
        ]
      }
    ]
  }
}
```

Значения темпа задаются в секундах на километр: `250` = 4:10/км, `260` =
4:20/км. Сервер сам конвертирует их в m/s, используемые Garmin Connect.

После preview вызовите `create_workout` с тем же объектом, датой `YYYY-MM-DD` и
`confirm=true`.

## Проверка

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
```

Unit-тесты не обращаются к Garmin и не требуют учётных данных.

## Важные ограничения

- Это не официальный Garmin Training API.
- Слишком частые логины могут получить HTTP 429; используйте сохранённые токены.
- Токены дают доступ к Garmin Connect — храните их как пароль.
- Если создание прошло, а планирование не прошло, инструмент вернёт
  `created_not_scheduled` и `workout_id`; созданный шаблон останется в библиотеке.
- Перед реальным использованием проверьте одну простую тренировку в Garmin Connect
  и на конкретной модели часов.
