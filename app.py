import os
from pathlib import Path

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
HEATMAP_USERS = [
    {"id": "pretend-zoe", "name": "Zoe", "active": True},
    {"id": "pretend-marcus", "name": "Marcus", "active": True},
    {"id": "pretend-jules", "name": "Jules", "active": True},
    {"id": "pretend-ava", "name": "Ava", "active": True},
    {"id": "pretend-owen", "name": "Owen", "active": True},
]

def build_heatmap_user_map() -> dict[str, list[str]]:
    slot_user_map: dict[str, list[str]] = {}
    for user in HEATMAP_USERS:
        for slot_id in store.load_availability(user_id=user["id"]):
            slot_user_map.setdefault(slot_id, []).append(user["id"])
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
    html = template.render(
        weekdays=WEEKDAYS,
        time_slots=build_time_slots(),
        user_id=user_id,
        user_readonly=True,
    )
    return HTMLResponse(content=html)


@app.get("/heatmap", response_class=HTMLResponse)
async def heatmap(request: Request) -> HTMLResponse:
    template = templates_env.get_template("availability.html")
    slot_counts = store.get_slot_counts()
    html = template.render(
        weekdays=WEEKDAYS,
        time_slots=build_time_slots(),
        user_id="",
        user_readonly=True,
        heatmap=True,
        slot_counts=slot_counts,
        heatmap_users=HEATMAP_USERS,
        slot_user_map=build_heatmap_user_map(),
    )
    return HTMLResponse(content=html)


@app.post("/availability")
async def save_availability(
    user_id: str = Form(...),
    selected_slots: str = Form(default=""),
) -> JSONResponse:
    selected = {slot for slot in selected_slots.split(",") if slot}
    store.save_availability(user_id=user_id, selected_slots=selected)
    return JSONResponse({"status": "ok", "saved": len(selected)})


@app.get("/availability")
async def load_availability(user_id: str) -> JSONResponse:
    return JSONResponse({"slots": sorted(store.load_availability(user_id=user_id))})
