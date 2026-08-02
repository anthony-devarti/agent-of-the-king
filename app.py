import os
from pathlib import Path
import re
import asyncio

import aiohttp

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from availability_service import AvailabilityStore, WEEKDAYS, build_slot_id, build_time_slots

app = FastAPI(title="Agent of the King Availability")
app.mount("/static", StaticFiles(directory="static"), name="static")

BASE_DIR = Path(__file__).resolve().parent
templates_env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"), autoescape=True)

store = AvailabilityStore()

DISCORD_USER_ID_RE = re.compile(r"^\d{17,20}$")
VALID_SLOT_IDS = {
    build_slot_id(day, time_slot)
    for day in WEEKDAYS
    for time_slot in build_time_slots()
}


def is_discord_user_id(value: str) -> bool:
    return bool(DISCORD_USER_ID_RE.match(str(value).strip()))


async def fetch_role_members(guild_id: str, role_id: str) -> list[dict[str, object]]:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or not guild_id or not role_id:
        return []

    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "AgentOfTheKing/1.0",
    }
    after = "0"
    members: list[dict[str, object]] = []
    seen_user_ids: set[str] = set()

    timeout = aiohttp.ClientTimeout(total=10, connect=5)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        while True:
            url = f"https://discord.com/api/v10/guilds/{guild_id}/members?limit=1000&after={after}"
            payload = None
            for attempt in range(2):
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            return members
                        payload = await response.json()
                    break
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                    return members

            if payload is None:
                return members

            if not isinstance(payload, list) or not payload:
                break

            for member in payload:
                member_roles = [str(value) for value in member.get("roles", [])]
                user = member.get("user", {})
                if str(role_id) not in member_roles:
                    continue
                if user.get("bot"):
                    continue

                discord_user_id = str(user.get("id") or "").strip()
                if not discord_user_id or discord_user_id in seen_user_ids:
                    continue
                seen_user_ids.add(discord_user_id)

                display_name = str(member.get("nick") or user.get("global_name") or user.get("username") or discord_user_id).strip()
                members.append(
                    {
                        "id": discord_user_id,
                        "name": display_name,
                    }
                )

            last_user = payload[-1].get("user", {})
            after = str(last_user.get("id", after))
            if len(payload) < 1000:
                break

    members.sort(key=lambda item: str(item.get("name") or "").lower())
    return members


async def build_heatmap_users(context: dict[str, object] | None) -> tuple[list[dict[str, object]], str | None, float]:
    saved_users = {user_id for user_id in store.list_user_ids() if is_discord_user_id(user_id)}
    if context:
        role_members = await fetch_role_members(
            str(context.get("guild_id") or ""),
            str(context.get("role_id") or ""),
        )
        users: list[dict[str, object]] = []
        group_a_ids: set[str] = set()

        if role_members:
            for member in role_members:
                user_id = str(member.get("id") or "").strip()
                if not is_discord_user_id(user_id):
                    continue
                group_a_ids.add(user_id)
                has_availability = user_id in saved_users
                users.append(
                    {
                        "id": user_id,
                        "name": str(member.get("name") or user_id),
                        "group": "A",
                        "group_label": "Game participants",
                        "active": has_availability,
                        "selectable": has_availability,
                    }
                )
        else:
            # Fallback when live Discord lookup fails.
            participant_name_by_id: dict[str, str] = {}
            for participant in context.get("participant_users", []):
                if not isinstance(participant, dict):
                    continue
                participant_id = str(participant.get("id") or "").strip()
                if not is_discord_user_id(participant_id):
                    continue
                participant_name = str(participant.get("name") or participant_id).strip() or participant_id
                participant_name_by_id[participant_id] = participant_name

            group_a_ids = {
                str(user_id)
                for user_id in context.get("participant_user_ids", [])
                if is_discord_user_id(str(user_id))
            }
            group_a_ids.update(participant_name_by_id.keys())
            for user_id in sorted(group_a_ids):
                has_availability = user_id in saved_users
                users.append(
                    {
                        "id": user_id,
                        "name": participant_name_by_id.get(user_id, user_id),
                        "group": "A",
                        "group_label": "Game participants",
                        "active": has_availability,
                        "selectable": has_availability,
                    }
                )

        for user_id in sorted(saved_users - group_a_ids):
            users.append(
                {
                    "id": user_id,
                    "name": user_id,
                    "group": "B",
                    "group_label": "Other users with availability",
                    "active": False,
                    "selectable": True,
                }
            )

        session_length_hours = float(context.get("session_length_hours") or 4)
        return users, str(context.get("game_name") or ""), session_length_hours

    # Fallback behavior when no game context is provided.
    return [
        {
            "id": user_id,
            "name": user_id,
            "group": "B",
            "group_label": "Other users with availability",
            "active": True,
            "selectable": True,
        }
        for user_id in sorted(saved_users)
    ], None, 4.0


def build_heatmap_user_map(user_ids: list[str]) -> dict[str, list[str]]:
    slot_user_map: dict[str, list[str]] = {}
    for user_id in user_ids:
        for slot_id in store.load_availability(user_id=user_id):
            slot_user_map.setdefault(slot_id, []).append(user_id)
    return slot_user_map


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user_id: str | None = None) -> HTMLResponse:
    template = templates_env.get_template("availability.html")
    html = template.render(
        weekdays=WEEKDAYS,
        time_slots=build_time_slots(),
        user_id=user_id or "",
        user_readonly=bool(user_id),
    )
    return HTMLResponse(content=html)


@app.get("/availability/{user_id}", response_class=HTMLResponse)
async def availability_for_user(request: Request, user_id: str) -> HTMLResponse:
    template = templates_env.get_template("availability.html")
    display_name = (request.query_params.get("display_name") or "").strip()
    html = template.render(
        weekdays=WEEKDAYS,
        time_slots=build_time_slots(),
        user_id=user_id,
        display_name=display_name,
        user_readonly=True,
    )
    return HTMLResponse(content=html)


@app.get("/heatmap", response_class=HTMLResponse)
async def heatmap(request: Request, context: str | None = None) -> HTMLResponse:
    template = templates_env.get_template("availability.html")
    heatmap_context = store.get_heatmap_context(context) if context else None
    heatmap_users, game_name, session_length_hours = await build_heatmap_users(heatmap_context)
    user_ids = [str(user["id"]) for user in heatmap_users]
    slot_counts = store.get_slot_counts()
    html = template.render(
        weekdays=WEEKDAYS,
        time_slots=build_time_slots(),
        user_id="",
        user_readonly=True,
        heatmap=True,
        heatmap_game_name=game_name,
        heatmap_session_length_hours=session_length_hours,
        slot_counts=slot_counts,
        heatmap_users=heatmap_users,
        slot_user_map=build_heatmap_user_map(user_ids),
    )
    return HTMLResponse(content=html)


@app.post("/availability")
async def save_availability(
    user_id: str = Form(...),
    selected_slots: str = Form(default=""),
) -> JSONResponse:
    cleaned_user_id = str(user_id).strip()
    if not is_discord_user_id(cleaned_user_id):
        return JSONResponse(
            {"status": "error", "error": "user_id must be a Discord user ID"},
            status_code=400,
        )

    selected = {slot for slot in selected_slots.split(",") if slot}
    invalid_slots = sorted(slot for slot in selected if slot not in VALID_SLOT_IDS)
    if invalid_slots:
        return JSONResponse(
            {"status": "error", "error": "selected_slots contains invalid slot IDs"},
            status_code=400,
        )

    store.save_availability(user_id=cleaned_user_id, selected_slots=selected)
    return JSONResponse({"status": "ok", "saved": len(selected)})


@app.get("/availability")
async def load_availability(user_id: str) -> JSONResponse:
    cleaned_user_id = str(user_id).strip()
    if not is_discord_user_id(cleaned_user_id):
        return JSONResponse(
            {"status": "error", "error": "user_id must be a Discord user ID"},
            status_code=400,
        )

    return JSONResponse({"slots": sorted(store.load_availability(user_id=cleaned_user_id))})
