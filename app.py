import os
from datetime import datetime, timezone
from pathlib import Path
import re
import asyncio
from zoneinfo import ZoneInfo

import aiohttp

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from availability_service import AvailabilityStore, WEEKDAYS, build_slot_id, build_time_slots
from KingSheets.character_sheet_store import CharacterSheetStore

app = FastAPI(title="Agent of the King Availability")
app.mount("/static", StaticFiles(directory="static"), name="static")

BASE_DIR = Path(__file__).resolve().parent
KING_SHEETS_DIR = BASE_DIR / "KingSheets"
KING_SHEETS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
app.mount("/KingSheets", StaticFiles(directory=KING_SHEETS_DIR), name="kingsheets")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")
king_sheets_templates_dir = KING_SHEETS_DIR / "templates"
king_sheets_templates_dir.mkdir(exist_ok=True)
templates_env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"), autoescape=True)

store = AvailabilityStore()
character_sheet_store = CharacterSheetStore()

DISCORD_USER_ID_RE = re.compile(r"^\d{17,20}$")
VALID_SLOT_IDS = {
    build_slot_id(day, time_slot)
    for day in WEEKDAYS
    for time_slot in build_time_slots()
}


def is_discord_user_id(value: str) -> bool:
    return bool(DISCORD_USER_ID_RE.match(str(value).strip()))


def discover_available_systems() -> list[dict[str, str]]:
    systems_dir = KING_SHEETS_DIR / "Systems"
    if not systems_dir.exists():
        return []

    systems_by_label: dict[str, dict[str, str]] = {}
    for pdf_path in sorted(systems_dir.rglob("*.pdf")):
        if not pdf_path.is_file():
            continue

        relative_dir = pdf_path.parent.relative_to(systems_dir)
        label = " ".join(relative_dir.parts) if relative_dir.parts else pdf_path.stem
        pdf_url = f"/KingSheets/{pdf_path.relative_to(KING_SHEETS_DIR).as_posix()}"
        systems_by_label[label] = {"label": label, "pdf_url": pdf_url}

    return sorted(systems_by_label.values(), key=lambda item: item["label"].lower())


def format_timestamp(value: str | None) -> str:
    if not value:
        return "—"

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    eastern = parsed.astimezone(ZoneInfo("America/New_York"))
    return eastern.strftime("%m/%d/%Y %I:%M %p")


def build_participant_name_map(context: dict[str, object] | None) -> dict[str, str]:
    if not context:
        return {}

    participant_name_by_id: dict[str, str] = {}
    for participant in context.get("participant_users", []):
        if not isinstance(participant, dict):
            continue
        participant_id = str(participant.get("id") or "").strip()
        if not is_discord_user_id(participant_id):
            continue
        participant_name = str(participant.get("name") or participant_id).strip() or participant_id
        participant_name_by_id[participant_id] = participant_name
    return participant_name_by_id


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
        participant_name_by_id = build_participant_name_map(context)
        if participant_name_by_id:
            store.upsert_user_profiles(
                [{"id": user_id, "name": name} for user_id, name in participant_name_by_id.items()],
                source="heatmap_context",
            )

        expected_group_a_ids = {
            str(user_id)
            for user_id in context.get("participant_user_ids", [])
            if is_discord_user_id(str(user_id))
        }
        expected_group_a_ids.update(participant_name_by_id.keys())

        role_members = await fetch_role_members(
            str(context.get("guild_id") or ""),
            str(context.get("role_id") or ""),
        )
        live_name_by_id: dict[str, str] = {}
        for member in role_members:
            user_id = str(member.get("id") or "").strip()
            if not is_discord_user_id(user_id):
                continue
            live_name_by_id[user_id] = str(member.get("name") or "").strip()

        if live_name_by_id:
            store.upsert_user_profiles(
                [{"id": user_id, "name": name} for user_id, name in live_name_by_id.items() if name],
                source="discord_role_members",
            )

        group_a_ids = set(expected_group_a_ids)
        group_a_ids.update(live_name_by_id.keys())
        known_user_ids = set(saved_users)
        known_user_ids.update(group_a_ids)
        persisted_name_by_id = store.get_user_profile_names(sorted(known_user_ids))

        users: list[dict[str, object]] = []
        for user_id in sorted(group_a_ids):
            has_availability = user_id in saved_users
            live_name = live_name_by_id.get(user_id, "")
            fallback_name = persisted_name_by_id.get(user_id, participant_name_by_id.get(user_id, user_id))
            users.append(
                {
                    "id": user_id,
                    "name": live_name or fallback_name,
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
                    "name": persisted_name_by_id.get(user_id, participant_name_by_id.get(user_id, user_id)),
                    "group": "B",
                    "group_label": "Other users with availability",
                    "active": False,
                    "selectable": True,
                }
            )

        session_length_hours = float(context.get("session_length_hours") or 4)
        return users, str(context.get("game_name") or ""), session_length_hours

    # Fallback behavior when no game context is provided.
    persisted_name_by_id = store.get_user_profile_names(sorted(saved_users))
    return [
        {
            "id": user_id,
            "name": persisted_name_by_id.get(user_id, user_id),
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


@app.get("/king-sheets", response_class=HTMLResponse)
async def king_sheets_page(request: Request, user_id: str | None = None, edit_system: str | None = None) -> HTMLResponse:
    sheets = []
    selected_sheet = None
    if user_id:
        sheets = character_sheet_store.list_character_sheets(owner_id=user_id)
        if edit_system:
            selected_sheet = character_sheet_store.load_character_sheet(owner_id=user_id, system_name=edit_system)
        elif sheets:
            selected_sheet = sheets[0]

    pdf_url = None
    if selected_sheet and selected_sheet.get("pdf_path"):
        pdf_url = f"/uploads/{Path(selected_sheet['pdf_path']).name}"

    template = Environment(loader=FileSystemLoader(king_sheets_templates_dir), autoescape=True).get_template("king_sheets.html")
    sheet_limit = 10
    saved_count = len(sheets)
    for sheet in sheets:
        sheet["created_display"] = format_timestamp(sheet.get("created_at"))
        sheet["updated_display"] = format_timestamp(sheet.get("updated_at"))
    create_disabled = saved_count >= sheet_limit
    html = template.render(
        user_id=user_id or "",
        sheets=sheets,
        sheet=selected_sheet,
        template_path="/KingSheets/Systems/World of Darkness/Mortals/NWoD1-Page_Editable.pdf",
        pdf_url=pdf_url,
        selected_system=selected_sheet.get("system_name") if selected_sheet else "World of Darkness Mortals",
        sheet_limit=sheet_limit,
        saved_count=saved_count,
        create_disabled=create_disabled,
        available_systems=discover_available_systems(),
    )
    return HTMLResponse(content=html)


@app.get("/king-sheets/wizard", response_class=HTMLResponse)
async def king_sheets_wizard_page(request: Request, user_id: str | None = None) -> HTMLResponse:
    template = Environment(loader=FileSystemLoader(king_sheets_templates_dir), autoescape=True).get_template("guided_wizard.html")
    html = template.render(
        user_id=user_id or "",
    )
    return HTMLResponse(content=html)


@app.post("/king-sheets/delete", response_model=None)
async def delete_king_sheet(request: Request) -> Response:
    form_data = await request.form()
    user_id = str(form_data.get("user_id") or "").strip()
    sheet_id = str(form_data.get("sheet_id") or "").strip()
    if not user_id or not sheet_id:
        return RedirectResponse(url="/king-sheets", status_code=303)

    character_sheet_store.delete_character_sheet(owner_id=user_id, sheet_id=sheet_id)
    redirect_url = f"/king-sheets?user_id={user_id}"
    return RedirectResponse(url=redirect_url, status_code=303)


@app.post("/king-sheets", response_model=None)
async def save_king_sheet(
    request: Request,
    user_id: str = Form(...),
    system_name: str = Form(default="World of Darkness Mortals"),
    character_name: str | None = Form(default=None),
    body_html: str | None = Form(default=None),
    pdf_file: UploadFile | None = File(default=None),
    sheet_id: str | None = Form(default=None),
    create_new: str | None = Form(default=None),
) -> Response:
    cleaned_user_id = str(user_id).strip()
    if not cleaned_user_id:
        return RedirectResponse(url="/king-sheets", status_code=303)

    accept_header = (request.headers.get("accept") or "").lower()
    requested_with = (request.headers.get("x-requested-with") or "").lower()
    form_data = await request.form()
    submitted_via_ajax = str(form_data.get("submitted_via_ajax") or "").strip().lower() == "true"
    wants_json = "application/json" in accept_header or requested_with == "xmlhttprequest" or submitted_via_ajax

    should_create_new = (create_new or "").strip().lower() == "true"
    existing_sheet = character_sheet_store.load_character_sheet(owner_id=cleaned_user_id, system_name=system_name, sheet_id=sheet_id) if not should_create_new else None
    preserved_character_name = existing_sheet.get("character_name") if existing_sheet else None
    preserved_body_html = existing_sheet.get("body_html") if existing_sheet else None
    preserved_title = existing_sheet.get("title") if existing_sheet else ""

    submitted_character_name = character_name.strip() if character_name is not None else None
    submitted_body_html = body_html.strip() if body_html is not None else None

    pdf_path = None
    pdf_filename = None
    if pdf_file and pdf_file.filename:
        file_name = Path(pdf_file.filename).name
        target_path = UPLOADS_DIR / f"{cleaned_user_id}_{file_name}"
        if target_path.exists():
            target_path.unlink()
        if existing_sheet and existing_sheet.get("pdf_path"):
            previous_path = Path(existing_sheet["pdf_path"])
            if previous_path.exists():
                previous_path.unlink()
        with target_path.open("wb") as handle:
            handle.write(await pdf_file.read())
        pdf_path = str(target_path)
        pdf_filename = file_name

    character_sheet_store.upsert_character_sheet(
        owner_id=cleaned_user_id,
        system_name=system_name,
        title=preserved_title,
        character_name=submitted_character_name if submitted_character_name is not None and submitted_character_name != "" else preserved_character_name or "",
        body_html=submitted_body_html if submitted_body_html is not None and submitted_body_html != "" else preserved_body_html or "",
        pdf_path=pdf_path,
        pdf_filename=pdf_filename,
        sheet_id=sheet_id,
        create_new=should_create_new,
    )

    redirect_url = f"/king-sheets?user_id={cleaned_user_id}"
    if wants_json:
        return JSONResponse({"status": "ok", "redirect_url": redirect_url})

    return RedirectResponse(url=redirect_url, status_code=303)
