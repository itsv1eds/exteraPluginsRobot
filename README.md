# exteraPluginsRobot

![Screenshot 1](img/welcome.png)

Telegram bot written with **aiogram** for managing an [@exteraPluginsSup](https://t.me/exteraPluginsSup) catalog, submissions, admin moderation, and group join settings (**Joinly**).

Live bot: [@exteraPluginsRobot](https://t.me/exteraPluginsRobot)

## Features

### Catalog
- **Plugins & iconpacks catalog** inside the bot.
- **Inline mode search**: type `@exteraPluginsRobot <query>`.
- **Plugin/icon preview pages** with links.

### Submissions & moderation
- **Upload and parse** `.plugin` / `.icons` files.
- **Submit suggestions/updates** for catalog items.
- **Admin moderation flow** (queues, review, publish/reject).

### Broadcast
- **Broadcast toggle for users** (opt-in/out).
- **Paid broadcast disable (Telegram Stars)**: user can pay to disable broadcast (for a joke, you can disable it for free).

### Joinly (group join settings)
Joinly is managed via **`/settings`** in a group (admins only).
- **Welcome message** with:
  - **MarkdownV2**
  - **Placeholders**: `{first}`, `{last}`, `{fullname}`, `{username}`, `{mention}`, `{id}`, `{chatname}`
  - **Inline buttons**: `[Text](buttonurl://https://example.com)` and `:same` to keep buttons in the same row
  - **Flags**: `{preview}`, `{nonotif}`, `{protect}`
- **Welcome on/off toggle** (independent from kick/ban).
- **Kick on join** (optional).
- **Ban on join** (optional; if disabled, uses kick-only flow).
- **Service message cleanup** (delete join/leave service messages).
- **Join reaction**: bot can react to the join service message when cleanup is disabled.

## Setup (classic)
1. Install dependencies
2. Configure `config.json`
3. Run:

```bash
python3 main.py
```
## Docker Compose
1. Configure `config.json` in project root.
2. Start bot:
   - `docker compose up -d --build`
3. View logs:
   - `docker compose logs -f bot`
4. Stop:
   - `docker compose down`
   
## SQLite storage
Storage backend is SQLite (`storage.sqlite3`).

Configure path in `config.json`:

```json
"storage": {
  "data_dir": "data/data",
  "sqlite_path": "data/data/storage.sqlite3"
}
```

You can also override at runtime with env vars:
- `DATA_DIR`
- `SQLITE_PATH`

## Userbot authorization (one-time)
Before running authorization, set in `config.json`:
- `userbot.api_id`
- `userbot.api_hash`

Run interactive authorization flow to create `sessions/userbot_session.session`:

`docker compose --profile tools run --rm auth`

In the prompt:
- enter phone number in international format (`+...`)
- enter code from Telegram
- if enabled, enter your 2FA password

After successful login, restart bot:

`docker compose restart bot`

## Re-authorization
If you need to log in with another account, remove previous session and run auth again:

`sudo rm -f sessions/userbot_session.session*`
