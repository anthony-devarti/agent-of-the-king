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
