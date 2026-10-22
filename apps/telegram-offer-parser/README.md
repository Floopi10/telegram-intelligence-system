# Telegram Offer Parser

Личный монитор офферов из групп и каналов, к которым уже имеет доступ твой Telegram-аккаунт.
Он не вступает в группы, не отвечает на сообщения и не совершает массовых действий.

## 1. Получи Telegram API-ключи

1. Открой <https://my.telegram.org>.
2. Войди по своему номеру.
3. Открой **API development tools**.
4. Создай приложение с любым понятным названием, например `Personal Offer Parser`.
5. Скопируй `api_id` и `api_hash`. Никому их не отправляй.

## 2. Установи проект (PowerShell)

Открой PowerShell в папке проекта и выполни:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
Copy-Item .env.example .env
notepad .env
```

В `.env` замени только эти значения:

```dotenv
TELEGRAM_API_ID=твой_api_id
TELEGRAM_API_HASH=твой_api_hash
```

Если прямой доступ к Telegram заблокирован, можно добавить HTTP/SOCKS-прокси:

```dotenv
PROXY_TYPE=http
PROXY_HOST=127.0.0.1
PROXY_PORT=10809
PROXY_USERNAME=
PROXY_PASSWORD=
```

Прокси должен быть запущен всё время работы парсера.

## 3. Авторизуй аккаунт

```powershell
offer-parser auth
```

Telegram запросит номер телефона, код из Telegram и пароль 2FA, если он включён.
После входа появится файл `data/account.session`. Это чувствительный файл: не отправляй его другим людям.

## 4. Получи список групп

```powershell
offer-parser scan-chats
```

Открой `data/chats.csv` в Excel. В колонке `enabled` замени `0` на `1` только у нужных групп и каналов. Сохрани CSV, не меняя его формат.

Повторный `scan-chats` обновляет список, но сохраняет уже выбранные `enabled=1`.

Если нужные чаты уже собраны в папки Telegram, ручной выбор не нужен:

```powershell
offer-parser folders
offer-parser select-folders --id 50 --id 142
```

Первая команда показывает ID папок, вторая заменяет текущий выбор содержимым указанных папок.

## 5. Проверь старые сообщения

Начни с небольшого объёма:

```powershell
offer-parser backfill --limit 100
offer-parser stats
offer-parser reclassify
```

Команда проверит последние 100 сообщений в каждом выбранном чате и сохранит совпадения в `data/offers.db`. Она специально не отправляет все старые совпадения в «Избранное».

## 6. Запусти мониторинг

```powershell
offer-parser monitor
```

Подходящие новые сообщения будут записываться в SQLite и приходить в «Избранное». Для остановки нажми `Ctrl+C`.

## Настройка качества

- Порог находится в `.env`: `MIN_SCORE=5`.
- Слова и их веса находятся в `rules.json`.
- Сильный сигнал имеет вес `5`, дополнительный — `1–3`, отрицательный — от `-3` до `-6`.
- Если мусора много, подними `MIN_SCORE` до `7`.
- Если офферы пропускаются, добавь реальные формулировки в `rules.json` или снизь порог.

После первых 100–200 размеченных сообщений правила следует откалибровать под твои реальные группы.

## Безопасность и ограничения

- Используй только собственные `api_id`/`api_hash`.
- Не публикуй `.env`, `.session` и базу с приватными сообщениями.
- Не запускай одну и ту же session одновременно на нескольких серверах.
- Не добавляй автоматические ответы и массовые рассылки: это резко увеличивает риск ограничений аккаунта.
- Уважай правила закрытых сообществ и не распространяй их сообщения без разрешения.
- При `FLOOD_WAIT` не перезапускай программу многократно; дождись указанного Telegram времени.

## Команды

```text
offer-parser auth
offer-parser diagnose
offer-parser scan-chats
offer-parser folders
offer-parser select-folders --id 50 --id 142
offer-parser backfill --limit 100
offer-parser monitor
offer-parser stats
offer-parser --debug monitor
```
