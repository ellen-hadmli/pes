# Hermes Agent: проверка конфигурации для Пса

Проверено по стабильному upstream tag **v0.22.2** (tag commit `d923a5eb`), не по `main`. Шаблон и генератор в этом handoff-пакете рассчитаны именно на эту схему. Здесь подготавливается переносимая конфигурация; установка, вход в Codex и запуск VPS выполняются на целевой машине.

## Что закладываем

- `model.provider: openai-codex`, `model.default: gpt-6-luna`, `agent.reasoning_effort: max`. В каталоге Codex-моделей релиза есть `gpt-6-luna`; правила effort включают для него `max`. Это подтверждение Hermes v0.22.2, не обещание доступности модели в конкретной подписке/аккаунте.
- `platform_toolsets.telegram` сохраняет штатный `hermes-telegram` целиком и добавляет `mcp-exa`. Инструменты harness не урезаются. Exa подключён к публичному `https://mcp.exa.ai/mcp`; README сервера говорит, что hosted MCP доступен анонимно с ограничением частоты. Это внешняя услуга и её лимиты могут меняться.
- Telegram rich rendering включён через `platforms.telegram.extra.rich_messages: true` (в tag это opt-in; Bot API 10.1 rich messages для таблиц, task lists, details и math). Telegram-клиенты могут отображать такие блоки по-разному. `reactions: false` штатно выключает processing reactions; `rich_drafts: false` оставляет streaming drafts выключенными, `allow_cjk_rich_messages: false` оставляет CJK на безопасном MarkdownV2 пути, `drop_pending_on_cold_boot: false` сохраняет доставку накопленных Telegram updates после cold boot, `guest_mode: false` не открывает неразрешённые группы.
- DM allowlist задаётся `allow_from: [owner_id]`, поэтому личный чат доступен только владельцу; owner chat ID также входит в `allowed_chats`. В adapter коде v0.22.2 выбирается ровно один ключ: `allow_from` для DM и `group_allow_from` для group/forum/channel. Генератор задаёт `group_allow_from: ["*"]`, явно разрешая любого участника перечисленных групп, а сами группы добавляет в `allowed_chats` и `group_allowed_chats`. `extra`-значения могут иметь приоритет над общими ключами, поэтому обе формы auth ключей выставляются в генерируемом Telegram block. Роли пользователей, slash ACL и сокращение групповых инструментов не используются.
- В группе включены `require_mention` и `observe_unmentioned_group_messages`. Разговор без обращения не запускает ответ, но сообщения из разрешённой группы попадают в её общий transcript как observed context. Ответ вызывают обычное Telegram-упоминание, reply боту или regex. Regex `^\s*п[её]с(?:ик)?\b` матчится без учёта регистра (включая «пёс», «пес», «пёсик», «песик») только с начала текста; длинная фраза с «пёс» внутри сама по себе не разбудит бота.
- Для получения всех обычных сообщений Telegram требует отключить privacy mode в BotFather либо дать боту права администратора; после изменения privacy mode бота нужно удалить из группы и добавить повторно. Это даёт боту возможность видеть сообщения группы — добавляйте его только туда, где это ожидаемо.
- В `channel_prompts` генератор кладёт группам инструкцию: самоизменение кода/skills/config только в личном чате с владельцем, не переносить личный или чужой контекст в другую переписку без явной просьбы владельца. Это штатный per-channel prompt, инжектируемый в ход модели. **Это best effort, а не граница безопасности**: prompt не блокирует shell, файлы, skills или другие harness tools. Если требуется технически гарантировать запрет самоизменения в группе, штатного conditional tool policy по типу Telegram-чата в проверенном теге не подтверждено; текущий выбор сознательно полагается на инструкцию модели.

## Иерархия и длительная история

В `gateway/config_loader.py::merge_platform_sections` релиза источники сливаются в порядке `gateway.platforms.*` → верхний `platforms.*` → shorthand `gateway.<platform>`; поздние общие значения выигрывают, `extra` объединяется. `platform_section` и bridge переносят общие Telegram ключи (`allowed_chats`, `group_allowed_chats`, `observe_unmentioned_group_messages`, `require_mention`, `mention_patterns`, `channel_prompts`) в конфигурацию адаптера. Поэтому генератор пишет в однозначный верхнеуровневый `platforms.telegram`, а опции рендеринга — в `platforms.telegram.extra`.

В конфигурацию добавлен явный `agent.system_prompt`: генератор подставляет абсолютный путь текущего checkout к проекту и `Library/README.md`, чтобы gateway daemon не зависел от текущей рабочей директории. Для истории включена штатная автоматическая compaction: `compression.enabled`, `threshold: 0.50`, `target_ratio: 0.20`, `tail_mode: lean`, `protect_last_n: 20`, `min_tail_user_messages: 1`, `in_place: true`, `progress_notices: false`. Она сохраняет один ID сессии и searchable soft-archive старых сообщений через `session_search`, но рабочий контекст сжимается — это не непрерывный дословный контекст. Обычная компактизация в Telegram по умолчанию не шумит прогресс-сообщениями.

В observed режиме неупомянутые сообщения входят в общий контекст своей разрешённой группы. Не просить модель переносить личный контекст между чатами полезно, но это не изоляция transcript. Не включать темы: в конфигурацию не добавляется `allowed_topics`, topic ID или `/topic`; личный чат остаётся root DM, группы работают в общих чатах.

## Подготовка и ограничения безопасности

`tools/configure.py` принимает `--owner-id` / `--group-id` (повторяемый) либо env `PES_OWNER_ID` / `PES_GROUP_IDS` (список через запятую). Токен читается только из отдельного файла через `--telegram-token-file` или `PES_TELEGRAM_TOKEN_FILE`. Скрипт не читает Hermes `auth.json`, не принимает токен значением CLI/env и не печатает его; он записывает токен в `~/.hermes/.env`, YAML — в `~/.hermes/config.yaml`, оба с правами `0600`. Существующий токен не заменяется без `--replace-token`; чужие ключи `.env` сохраняются. Сам файл входного токена остаётся у вызывающего и его нужно хранить безопасно/удалить по собственному процессу.

Пример для целевого Linux VPS после установки Hermes и получения bot token отдельным безопасным файлом:

```sh
python3 tools/configure.py --owner-id 123456789 --group-id -1001234567890 \
  --telegram-token-file /secure/path/telegram-token
```

Group IDs — ID чатов; owner ID — Telegram user/chat ID для DM allowlist. `group_allow_from: ["*"]` разрешает всем участникам только перечисленных групп. Публичный hosted Exa не требует ключа, но может ограничивать анонимный трафик. `mcp-exa` должен поддерживаться установленным Hermes toolset resolver; шаблон использует штатное именование `mcp-<server>` для MCP tools.

## Первичные источники

- [Telegram setup, observe mode, mention patterns, BotFather privacy](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/website/docs/user-guide/messaging/telegram.md)
- [Gateway config loader and platform merge/bridge](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/gateway/config_loader.py)
- [Telegram adapter: rich messages, reactions, regex processing](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/plugins/platforms/telegram/adapter.py)
- [Per-channel prompt injection](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/gateway/platforms/base.py)
- [v0.22.2 config example: models, compression, platform toolsets, MCP](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/cli-config.yaml.example)
- [Codex model catalog](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/hermes_cli/codex_models.py) and [reasoning effort support](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/agent/reasoning_effort.py)
- [Compaction and session archive behavior](https://github.com/NousResearch/hermes-agent/blob/v0.22.2/website/docs/developer-guide/context-compression-and-caching.md)
- [Exa MCP upstream README (anonymous hosted endpoint/rate limit)](https://github.com/exa-labs/exa-mcp-server/blob/main/README.md)
