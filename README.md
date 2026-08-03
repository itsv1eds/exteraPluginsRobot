# exteraPluginsRobot

![Screenshot 1](img/welcome.png)

Telegram bot written with **aiogram** for managing the [@exteraPluginsSup](https://t.me/exteraPluginsSup) catalog: submissions, moderation voting, channel publishing, scheduled posts, and group join settings (**Joinly**).

Live bot: [@exteraPluginsRobot](https://t.me/exteraPluginsRobot)

## Features

### Catalog
- **Plugins & iconpacks catalog** inside the bot, filterable by category and source.
- **Inline search**: type `@exteraPluginsRobot <query>` in any chat.
- **Subscriptions**: get notified when a plugin you follow is updated.

### Submissions
- **Upload and parse** `.plugin` / `.icons` files (up to 8 MB).
- **Rules quiz** before submitting: 3 random questions out of 20, four options each.
- **Validation**: all fields required, minimum client version `12.1.1` (configurable), duplicate and blocklist checks.
- **Updates and removal requests** for already published items, with a mandatory reason.
- **Profile**: your published plugins plus everything currently under review.

### Moderation
- **Voting in a forum topic** with a configurable threshold.
- **Mandatory vote reason**: anonymous, from a template, or custom — separate template sets for approve and reject.
- **Reasons are sent to the author** by default (can be turned off) as a reply to their original submission.
- **Appeals**: an author can resubmit a rejected plugin with a comment; a second rejection puts the plugin id on a blocklist.
- **Author contact**: replies go to the forum, not to a single moderator's DM.
- **Audit log** of every action, with filters, plus a record of who approved or rejected each request.
- **Rejected files are kept for 7 days** in case the decision is reversed.

### Poster
- **Scheduled channel posts** with a custom UTC offset, repeat and auto-delete.
- **Attachments**: photo, video, GIF, audio or any file.
- **Rich messages** (Bot API 10.1): headings, lists, tables, quotes, collapsible blocks — up to 32768 characters.
- **Publish now** and inline preview of any scheduled post.

### Admin panel
- **All requests** grouped by status, sorted so unvoted ones come first.
- **Catalog editing**, sources, plugin authors linking, bans and blocklist.
- **Per-admin notification settings**, backups, health check and maintenance tools.

### Broadcast
- **Broadcast toggle for users** (opt-in/out).
- **Paid broadcast disable (Telegram Stars)**: user can pay to disable broadcast (for a joke, you can disable it for free).

### Joinly (group join settings)
Managed via **`/settings`** in a group (admins only).
- **Welcome message** with:
  - **MarkdownV2**
  - **Placeholders**: `{first}`, `{last}`, `{fullname}`, `{username}`, `{mention}`, `{id}`, `{chatname}`
  - **Inline buttons**: `[Text](buttonurl://https://example.com)` and `:same` to keep buttons in the same row
  - **Flags**: `{preview}`, `{nonotif}`, `{protect}`
- **Welcome on/off toggle** (independent from kick/ban).
- **Kick on join** and **ban on join** (optional).
- **Service message cleanup** and **join reaction**.

## Commands
- `/start` — main menu, also handles deep links
- `/admin` — admin panel
- `/lang` — switch language (Russian / English)
- `/new` — resend active requests to the moderation forum (super admins)
- `/settings` — Joinly settings in a group
- `/unlockchat` — unlock a chat

## Setup
Requires **Python 3.12**.

1. `pip install -r requirements.txt`
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
Storage backend is SQLite (`storage.sqlite3`). Runtime data and app config are
stored in SQLite; legacy `database*.json` files are not read.

The bot can bootstrap storage path from `app_config` inside SQLite. You can
override paths at runtime with env vars:

- `DATA_DIR`
- `SQLITE_PATH`

## Userbot authorization (one-time)
A Telegram Premium userbot publishes catalog posts (custom emoji, larger
captions) alongside the bot. Before running authorization, set in `config.json`:
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
