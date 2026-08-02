# Agent of the King

Agent of the King is a Discord bot for Arkham Horror LCG helpers plus a browser-based weekly availability planner. It currently supports ArkhamDB card and deck lookups in Discord, a persistent availability editor backed by SQLite, and a read-only heatmap view for comparing recurring weekly schedules.

## Current Features

### Discord bot
The bot responds to message content and slash commands.

Message-driven features:
* Card lookup using `[[card name]]` syntax.
* Exact-name matching first, then substring fallback, then fuzzy fallback.
* Optional level filtering such as `[[.41 Derringer (0)]]` or upgraded `[[Shrivelling (u)]]`.
* ArkhamDB deck and decklist link expansion into Discord embeds.
* Large responses can be moved into a thread automatically.

### Availability web app
The web app is served by FastAPI and stores recurring weekly availability in SQLite.

Current behavior:
* 7-day weekly editor with 30-minute time slots.
* Availability is persisted in `availability.sqlite`.
* User-specific editor routes such as `/availability/<user_id>`.
* Read-only heatmap view at `/heatmap`.
* Heatmap filtering by user.
* Recommendation highlighting for strong shared 4-hour windows.
* User lock controls in the heatmap so selected users can be treated as mandatory for recommendations.

## Slash Commands

This is the full current slash-command surface in the bot.

* `/availability`
    Opens the browser-based weekly availability editor for the invoking Discord user. The bot returns a user-specific editor URL.

* `/heatmap`
    Opens the shared availability heatmap page in a button link.

* `/hi`
    Sends a short introduction describing the bot's ArkhamDB and availability capabilities.

* `/sync_commands`
    Forces a Discord slash-command sync. Useful when command definitions have changed and need to be refreshed in Discord.

* `/reload_cards`
    Reloads the ArkhamDB card cache without restarting the bot.

* `/admin add_game`
    Server-admin only. Opens a form to capture game name, then lets the admin choose the participant role from server roles (excluding bot-managed roles). Adds the game to the active list or reactivates it if previously inactive.

* `/admin remove_game`
    Server-admin only. Opens a game dropdown and requires a confirmation click before marking a game inactive.

* `/admin list_games`
    Server-admin only. Lists active games.

## HTTP Routes

The availability app currently exposes these routes:

* `/`
    Renders the availability editor. A `user_id` query parameter can prefill the editor identity.

* `/availability/<user_id>`
    Renders the availability editor for a specific user.

* `GET /availability?user_id=<user_id>`
    Returns saved slot IDs for that user as JSON.

* `POST /availability`
    Saves availability for a user using form fields `user_id` and `selected_slots`.

* `/heatmap`
    Renders the read-only heatmap page using persisted data and real users with saved availability.

## Tech Stack

* Python
* `discord.py`
* FastAPI
* Jinja2
* SQLite
* `aiohttp`
* `rapidfuzz`

## Local Development

### Setup
This project currently expects Python 3 and a virtual environment.

1. Clone the repository.
2. Create a virtual environment:
     `python3 -m venv .venv`
3. Activate it:
     `. .venv/bin/activate`
4. Install dependencies:
     `pip install -r requirements.txt`

### Secrets
Preferred options:

* GitHub Codespaces secret named `DISCORD_TOKEN`
* Local untracked `.env` file for non-Codespaces development

The bot reads:

* `DISCORD_TOKEN`
* `DISCORD_GUILD_ID` optional, for guild-specific slash sync
* `AVAILABILITY_WEB_URL` optional, defaults to `http://127.0.0.1:8000/`
* `AVAILABILITY_EDITOR_WEB_URL` optional, defaults to `AVAILABILITY_WEB_URL` and is used by `/availability` editor links
* `ALLOWED_CHANNEL_IDS` optional, comma-separated allowlist

If using a local `.env` file:

1. Copy `.env.example` to `.env`
2. Fill in local values
3. Do not commit `.env`

### Run the Discord bot
`python3 agent-of-the-king.py`

### Run the availability web app
`uvicorn app:app --host 127.0.0.1 --port 8000`

### Run tests
`pytest -q`

## Pre-commit Secret Guard

This repo includes a pre-commit hook at `.githooks/pre-commit`.

What it does:
* Blocks commits if `.env` is staged as a new or modified file.
* Blocks commits when staged diffs contain token-like or secret-like values.
* Prints local cleanup instructions so you can unstage and fix the content before recommitting.

Enable it once per clone:
`git config core.hooksPath .githooks`

## Persistence Notes

Availability is stored in `availability.sqlite` in a single `availability` table keyed by `(user_id, slot_id)`.

Each saved slot is a recurring weekly slot such as:
* `Monday:19:00`
* `Wednesday:20:30`

## Docker

This repository includes a `Dockerfile` for containerized runs.

Typical usage:
* `docker build -t agent-of-the-king ./`
* `docker run --name agent-of-the-king --restart always -d agent-of-the-king`

Published GHCR image:
* `ghcr.io/anthony-devarti/agent-of-the-king:latest`

### Unraid Clean Update Runbook

Use this when you want to pull and restart both containers in a repeatable way.

Prereqs:
* Your bot container is named `agent-of-the-king`.
* Your web container is named `agent-of-the-king-web`.
* Persistent data is mounted at `/mnt/user/appdata/agent-of-the-king`.

Important:
* Do not use hostnames like `Svalbard` in link URLs unless every user can resolve that name.
* Use either a reachable LAN IP (for local-only users) or a public domain (for remote users).

#### 1) Update web container (FastAPI)

```bash
docker rm -f agent-of-the-king-web 2>/dev/null || true
docker pull ghcr.io/anthony-devarti/agent-of-the-king:latest
docker run -d \
    --name agent-of-the-king-web \
    --restart unless-stopped \
    -p 8000:8000 \
    -v /mnt/user/appdata/agent-of-the-king:/data \
    -e AVAILABILITY_DB_PATH=/data/availability.sqlite \
    ghcr.io/anthony-devarti/agent-of-the-king:latest \
    python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
```

#### 2) Update bot container (Discord)

This reuses your current token/guild/channel settings from the existing container.

```bash
IP=$(hostname -I | awk '{print $1}')
TOKEN=$(docker inspect agent-of-the-king --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^DISCORD_TOKEN=//p')
GUILD=$(docker inspect agent-of-the-king --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^DISCORD_GUILD_ID=//p')
ALLOWED=$(docker inspect agent-of-the-king --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^ALLOWED_CHANNEL_IDS=//p')

docker rm -f agent-of-the-king 2>/dev/null || true
docker pull ghcr.io/anthony-devarti/agent-of-the-king:latest
docker run -d \
    --name agent-of-the-king \
    --restart unless-stopped \
    -v /mnt/user/appdata/agent-of-the-king:/data \
    -e DISCORD_TOKEN="$TOKEN" \
    -e AVAILABILITY_DB_PATH=/data/availability.sqlite \
    -e AVAILABILITY_WEB_URL="http://$IP:8000" \
    -e AVAILABILITY_EDITOR_WEB_URL="http://$IP:8000" \
    ${GUILD:+-e DISCORD_GUILD_ID="$GUILD"} \
    ${ALLOWED:+-e ALLOWED_CHANNEL_IDS="$ALLOWED"} \
    ghcr.io/anthony-devarti/agent-of-the-king:latest
```

If you have a public domain, replace both `AVAILABILITY_*_URL` values with that domain.

#### 3) Verify health

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'agent-of-the-king(-web)?'
docker logs --tail 50 agent-of-the-king-web
docker logs --tail 50 agent-of-the-king
docker inspect agent-of-the-king --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^AVAILABILITY_WEB_URL=|^AVAILABILITY_EDITOR_WEB_URL='
```

Expected:
* Both containers are `Up`.
* Web logs show Uvicorn started on port `8000`.
* Bot env includes both `AVAILABILITY_WEB_URL` and `AVAILABILITY_EDITOR_WEB_URL`.
