# exteraPluginsRobot

![Screenshot 1](img/welcome.jpg)

Telegram bot written with **aiogram** for managing the [@exteraPluginsSup](https://t.me/exteraPluginsSup) catalog: submissions, moderation voting, channel publishing, scheduled posts, and group join settings (**Joinly**).

Live bot: [@exteraPluginsRobot](https://t.me/exteraPluginsRobot)

## Features

### Catalog
- **Plugins & iconpacks catalog** inside the bot, filterable by category and source.
- **Inline search**: type `@exteraPluginsRobot <query>` in any chat.
- **Subscriptions**: get notified when a plugin you follow is updated.

### Submissions
- **Upload and parse** `.plugin` (up to 8 MB) and Elyx `.elyx` / `.eaf` archives (up to 100 MB), including `.elyx.zip` / `.eaf.zip`. Updates can migrate from Python to Elyx with the same plugin id. Iconpacks remain in the catalog only.
- **Rules quiz** before submitting: 3 random questions out of 20, four options each.
- **Validation**: all fields required, minimum client version `12.1.1` (configurable), duplicate and blocklist checks.
- **Updates and removal requests** for already published items, with a mandatory reason.
- **Profile**: your published plugins plus everything currently under review.

### Moderation
- **Voting in a forum topic** with a configurable threshold.
- **Mandatory vote reason**: anonymous, from a template, or custom — separate template sets for approve and reject.
- **Reasons are sent to the author** by default (can be turned off) as a reply to their original submission.
- **Appeals**: an author can resubmit a rejected plugin with a comment; a second rejection puts the plugin id on a blocklist.
- **Author contact**: replies are delivered to the conversation's recipient; forum replies to authors require confirmation.
- **Audit log** of every action, with filters, plus a record of who approved or rejected each request.
- **Request files and previous versions are retained**, including after rejection or publication.

### Poster
- **Scheduled channel posts** with a custom UTC offset, repeat and auto-delete.
- **Attachments**: photo, video, GIF, audio or any file.
- **Rich messages** (Bot API 10.1): headings, lists, tables, quotes, collapsible blocks — up to 32768 characters.
- **Publish now** and inline preview of any scheduled post.

### Admin panel
- **All requests** grouped by status, sorted so unvoted ones come first.
- **Catalog editing**, plugin authors linking, bans and blocklist.
- **Per-admin notification settings**, backups, health check and maintenance tools.

### Broadcast
- **Broadcast toggle for users** (opt-in/out).
- **Paid broadcast disable (Telegram Stars)**: user can pay to disable broadcast (for a joke, you can disable it for free).

### Joinly (group join settings)
Managed via **`/settings`** in a group or **Profile → Joinly** (chat admins only).
- **Welcome message** with:
  - **MarkdownV2**
  - **Placeholders**: `{first}`, `{last}`, `{fullname}`, `{username}`, `{mention}`, `{id}`, `{chatname}`
  - **Inline buttons**: `[Text](buttonurl://https://example.com)` and `:same` to keep buttons in the same row
  - **Flags**: `{preview}`, `{nonotif}`, `{protect}`
- **Welcome on/off toggle** (independent from kick/ban).
- **Public or personal welcome**: the personal mode uses Telegram ephemeral messages visible only to the new member. Requires the bot to be an admin with permission to send welcome messages; delivery to offline members is not guaranteed.
- **Personal preview** and an **exteraPlugins template** linking to the catalog, security post and publishing rules. Existing chat templates remain unchanged until selected explicitly.
- Channel welcome messages are configured in Telegram: **channel profile → Edit → Welcome Message**.
- **Kick on join** and **ban on join** (optional).
- **Service message cleanup** and **join reaction**.

## Commands
- `/start` — main menu, also handles deep links
- `/admin` — admin panel
- `/lang` — switch language (Russian / English / Ukrainian / Simplified Chinese)
- `/new` — resend active requests to the moderation forum (super admins)
- `/settings` — Joinly settings in a group
- `/unlockchat` — unlock a chat

## Setup
Requires **Python 3.12**.

Interface texts are stored separately in `bot/locales/{ru,en,ua,zh}.json`.
Inline search accepts a language suffix: `@exteraPluginsRobot plugin en` (`ru`, `en`, `ua`, `zh`).
Moderation reason translations and quiz seeds are in the `reasons/` and `quiz/`
subdirectories. Include the entire `bot/locales/` directory when deploying.
Template reasons are shown in the recipient's selected language; custom text is
preserved as written.

1. `pip install -r requirements.txt`
2. Set `DATA_DIR` to your data directory. An existing `storage.sqlite3` is migrated automatically; keep a backup and stop the old bot first.
3. For a fresh database, copy `config.json.example` to `config.json`, fill in the owner and channel IDs, then import it with `python -c 'import json; from core.storage import save_config; save_config(json.load(open("config.json")))'`.
4. Provide credentials through `BOT_TOKEN`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` or the private `DATA_DIR/private/runtime.json` file. Existing database credentials are extracted during migration.
5. Run:

```bash
python3 main.py
```

## Docker Compose
1. Put data in `./data` and userbot sessions in `./sessions`. Existing `./data/storage.sqlite3` is migrated on startup. Runtime credentials belong in `./data/private/runtime.json`; no root `config.json` mount is needed.
2. Start bot:
   - `docker compose up -d --build`
3. View logs:
   - `docker compose logs -f bot`
4. Stop:
   - `docker compose down`

## SQLite storage
`catalog.sqlite3` contains plugins and iconpacks. `admin.sqlite3` contains requests,
users, moderation, statistics and other service data. Secrets are stored separately
in `private/runtime.json` or supplied through environment variables. The obsolete
wall and `meta_store` table are not copied into the new databases.

Paths can be overridden with `DATA_DIR`, `CATALOG_SQLITE_PATH`,
`ADMIN_SQLITE_PATH`, `LEGACY_SQLITE_PATH` and `BOT_SECRETS_FILE`.
Bot backups include the databases, the journal and uploaded files. Tokens, API
credentials and sessions are excluded. Restore the databases together and keep
runtime credentials and sessions separately on the server.

`archive.sqlite3` holds the durable journal. Under **Settings → Channels**,
super-admins can set two private channels. The moderation log shows request
cards, decisions and tagged actions. The technical archive receives compressed
JSON Lines batches and original files. Large files are split into numbered parts
with a common SHA-256. The bot needs permission to post in both channels. Enter
`0` to pause either feed. Events and files remain on the server and delivery
resumes after a restart.
Set `archive.data_quota_bytes` to the hosting storage limit to pause background
media downloads before the volume fills. Pending media stay queued.
Submitted requests, expired drafts, votes, decisions and uploaded versions are
retained permanently.

## Userbot authorization (one-time)
A Telegram Premium userbot publishes catalog posts (custom emoji, larger
captions) alongside the bot. Before running authorization, set in the private runtime file (or use the corresponding environment variables):
- `userbot.api_id`
- `userbot.api_hash`

Create and activate a fresh session on the same host as the bot:

`docker compose --profile tools run --build --rm auth python auth.py --new-session --activate`

In the prompt:
- enter phone number in international format (`+...`)
- enter code from Telegram
- if enabled, enter your 2FA password

After successful login, restart bot:

`docker compose restart bot`

## Re-authorization
For another account or a revoked key, repeat authorization with `--new-session --activate`. Each running instance must authorize independently: copying a `.session` file does not create a new key and can cause Telegram to revoke it. Enter the login code and 2FA password only in the terminal; both inputs are hidden.
