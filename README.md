# exteraPluginsRobot
![Screenshot 1](img/welcome.png)
Just a base bot for Telegram, writen on aiogram for my [Telegram channel](https://t.me/exteraPluginsSub)

Fully working bot: [@exteraPluginsRobot](https://t.me/exteraPluginsRobot)

# Features:
- Plugins/Iconpacks Catalog in bot and inline with search by search in descripsion or name (@exteraPluginsRobot ...)
- Suggested plugins/iconpacks
- Kicks everybody who trying to join your group.
- Parse data from file .plugin/.icons

# Setup (Classic)
1. Install dependencies
2. Configure `config.json`
3. Run bot by ```python3 main.py```

# SQLite storage 02/26/25
Storage backend is now SQLite (`storage.sqlite3`).

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

## Full migration guide (Docker)
The goal is to migrate legacy JSON storage (`data/data/*.json`) to SQLite.

### 1. Stop bot and create backup
```bash
docker compose down
ts=$(date +%F_%H-%M-%S)
mkdir -p backups/$ts
cp -a data backups/$ts/data
cp -a config.json backups/$ts/config.json
```

### 2. Ensure config has SQLite path
```json
"storage": {
  "data_dir": "data/data",
  "sqlite_path": "data/data/storage.sqlite3"
}
```

### 3. Build image
```bash
docker compose build bot
```

### 4. Run migration
If your legacy data is already in `./data/data` and uploads in `./data/uploads`:
```bash
docker compose run --rm -e PYTHONIOENCODING=utf-8 bot \
  python scripts/migrate_json_to_sqlite.py \
  --source-dir /app/data/data \
  --sqlite-path /app/data/data/storage.sqlite3 \
  --uploads-dir /app/data/uploads \
  --strict
```

If you have a separate dump (for example `shit/data` + `shit/uploads`), first copy it:
```bash
mkdir -p data/data data/uploads
cp -a shit/data/* data/data/
cp -a shit/uploads/* data/uploads/
```

### 5. Smoke check
```bash
docker compose run --rm -e PYTHONIOENCODING=utf-8 bot python sync_channel.py status
```

### 6. Start bot
```bash
docker compose up -d bot
docker compose logs -f --tail=200 bot
```

### 7. Optional cleanup of legacy JSON after successful migration
Only after successful smoke/start and with backups ready:
```bash
docker compose down
rm -f data/data/databaseplugins.json \
      data/data/databaseicons.json \
      data/data/databaserequests.json \
      data/data/databasesubscriptions.json \
      data/data/databaseupdated.json \
      data/data/users.json \
      data/data/databaseusers.json \
      data/data/snowflake_db.json
docker compose up -d bot
```

## Full migration guide (Classic, without Docker)
### 1. Create backup
```bash
ts=$(date +%F_%H-%M-%S)
mkdir -p backups/$ts
cp -a data backups/$ts/data
cp -a config.json backups/$ts/config.json
```

### 2. Install dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure storage in `config.json`
```json
"storage": {
  "data_dir": "data/data",
  "sqlite_path": "data/data/storage.sqlite3"
}
```

### 4. Run migration
```bash
python scripts/migrate_json_to_sqlite.py \
  --source-dir data/data \
  --sqlite-path data/data/storage.sqlite3 \
  --uploads-dir data/uploads \
  --strict
```

### 5. Smoke check
```bash
PYTHONIOENCODING=utf-8 python sync_channel.py status
```

### 6. Start bot
```bash
PYTHONIOENCODING=utf-8 python main.py
```

### 7. Optional cleanup of legacy JSON
```bash
rm -f data/data/databaseplugins.json \
      data/data/databaseicons.json \
      data/data/databaserequests.json \
      data/data/databasesubscriptions.json \
      data/data/databaseupdated.json \
      data/data/users.json \
      data/data/databaseusers.json \
      data/data/snowflake_db.json
```

Compatibility note:
- If SQLite is empty, storage reads legacy JSON (if present) and persists data to SQLite on first access.
- Migration script drops legacy `kv_store` table by default.
- Use `--keep-kv-store` if you want to keep it temporarily for rollback/debug.

# Docker Compose
1. Configure `config.json` in project root.
2. Start bot:
   - `docker compose up -d --build`
3. View logs:
   - `docker compose logs -f bot`
4. Stop:
   - `docker compose down`

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
