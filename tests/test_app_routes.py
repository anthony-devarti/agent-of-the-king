from pathlib import Path
import asyncio

from fastapi.testclient import TestClient

import app
from availability_service import build_slot_id


def test_home_page_uses_query_params_and_marks_user_readonly():
    client = TestClient(app.app)

    response = client.get("/", params={"user_id": "discord-123"})

    assert response.status_code == 200
    body = response.text
    assert 'name="user_id"' in body
    assert 'readonly' in body
    assert 'value="discord-123"' in body


def test_save_availability_endpoint_accepts_user_only_payload():
    client = TestClient(app.app)

    response = client.post(
        "/availability",
        data={"user_id": "123456789012345678", "selected_slots": "Monday:09:00,Wednesday:20:30"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "saved": 2}


def test_save_availability_rejects_non_discord_user_id():
    client = TestClient(app.app)

    response = client.post(
        "/availability",
        data={"user_id": "discord-123", "selected_slots": "Monday:09:00"},
    )

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_save_availability_rejects_invalid_slot_ids():
    client = TestClient(app.app)

    response = client.post(
        "/availability",
        data={"user_id": "123456789012345678", "selected_slots": "Notaday:25:61"},
    )

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_load_availability_rejects_non_discord_user_id():
    client = TestClient(app.app)

    response = client.get("/availability", params={"user_id": "discord-123"})

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_availability_page_keeps_selected_slots_inside_form_for_submission():
    client = TestClient(app.app)

    response = client.get("/availability/alex-test")

    assert response.status_code == 200
    body = response.text
    form_start = body.index('<form id="availability-form">')
    form_end = body.index('</form>', form_start)
    form_content = body[form_start:form_end]
    assert 'name="selected_slots"' in form_content
    assert 'class="slot-box"' in body
    assert 'data-day="Monday"' in body


def test_heatmap_page_renders_read_only_grid():
    client = TestClient(app.app)

    response = client.get("/heatmap")

    assert response.status_code == 200
    body = response.text
    assert 'Availability' in body
    assert 'data-heatmap="true"' in body
    assert 'class="slot-box"' in body


def test_availability_page_uses_touch_friendly_slot_controls():
    client = TestClient(app.app)

    response = client.get("/availability/alex-test")

    assert response.status_code == 200
    body = response.text
    assert 'class="slot-box"' in body
    assert 'data-day="Monday"' in body


def test_heatmap_frontend_scales_cell_intensity_from_slot_counts():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    content = app_js.read_text()

    assert "if (form) {" in content
    assert "visibleMaxCount" in content
    assert "Math.min(1, count / visibleMaxCount)" in content


def test_heatmap_renders_interactive_user_filters():
    client = TestClient(app.app)
    app.store.save_availability(
        user_id="111111111111111111",
        selected_slots={build_slot_id("Monday", "09:00")},
    )

    response = client.get("/heatmap")

    assert response.status_code == 200
    body = response.text
    assert 'class="user-filter-groups"' in body
    assert 'id="user-availability-data"' in body
    assert '111111111111111111' in body


def test_heatmap_renders_live_recommendation_summary():
    client = TestClient(app.app)

    response = client.get("/heatmap")

    assert response.status_code == 200
    body = response.text
    assert 'id="recommendation-summary"' in body
    assert 'data-time-slots=' in body


def test_heatmap_renders_user_lock_controls():
    client = TestClient(app.app)
    app.store.save_availability(
        user_id="222222222222222222",
        selected_slots={build_slot_id("Tuesday", "10:30")},
    )

    response = client.get("/heatmap")

    assert response.status_code == 200
    body = response.text
    assert 'id="user-availability-data"' in body
    assert 'data-users=' in body


def test_heatmap_recommendation_logic_supports_locked_users():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    content = app_js.read_text()

    assert 'userLockState' in content
    assert 'lockedUserIds' in content
    assert 'is-locked' in content


def test_heatmap_user_labels_are_not_injected_via_innerhtml():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    content = app_js.read_text()

    assert 'toggleButton.innerHTML' not in content
    assert 'toggleLabel.textContent' in content


def test_heatmap_recommendation_copy_uses_variable_session_length_window():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    content = app_js.read_text()

    assert 'sessionLengthHours' in content
    assert 'sessionSlotCount' in content
    assert 'full ${sessionLengthHours}-hour block' in content


def test_heatmap_group_a_uses_live_discord_ids_and_names_id_only(monkeypatch):
    app.store.save_availability(
        user_id="333333333333333333",
        selected_slots={build_slot_id("Monday", "09:00")},
    )
    app.store.save_availability(
        user_id="legacy-name-user",
        selected_slots={build_slot_id("Tuesday", "10:00")},
    )

    async def fake_fetch_role_members(guild_id: str, role_id: str):
        return [
            {
                "id": "333333333333333333",
                "name": "Alice Smith",
                "aliases": ["333333333333333333", "alice-smith"],
            }
        ]

    monkeypatch.setattr(app, "fetch_role_members", fake_fetch_role_members)

    users, game_name, session_length_hours = asyncio.run(
        app.build_heatmap_users(
            {
                "game_name": "Hotel California",
                "session_length_hours": 4.0,
                "guild_id": "1",
                "role_id": "2",
                "participant_user_ids": ["ignored-fallback"],
            }
        )
    )

    group_a = [user for user in users if user["group"] == "A"]
    assert len(group_a) == 1
    assert group_a[0]["id"] == "333333333333333333"
    assert group_a[0]["name"] == "Alice Smith"
    assert group_a[0]["active"] is True
    assert group_a[0]["selectable"] is True
    assert game_name == "Hotel California"
    assert session_length_hours == 4.0

    group_b_ids = {str(user["id"]) for user in users if user["group"] == "B"}
    assert "legacy-name-user" not in group_b_ids


def test_heatmap_fallback_uses_context_participant_names(monkeypatch):
    app.store.save_availability(
        user_id="555555555555555555",
        selected_slots={build_slot_id("Wednesday", "14:00")},
    )

    async def fake_fetch_role_members(guild_id: str, role_id: str):
        return []

    monkeypatch.setattr(app, "fetch_role_members", fake_fetch_role_members)

    users, _, _ = asyncio.run(
        app.build_heatmap_users(
            {
                "game_name": "Kings Game",
                "session_length_hours": 4.0,
                "guild_id": "1",
                "role_id": "2",
                "participant_user_ids": ["555555555555555555"],
                "participant_users": [
                    {"id": "555555555555555555", "name": "Alice Smith"},
                ],
            }
        )
    )

    group_a = [user for user in users if user["group"] == "A"]
    assert len(group_a) == 1
    assert group_a[0]["id"] == "555555555555555555"
    assert group_a[0]["name"] == "Alice Smith"


def test_heatmap_user_map_uses_discord_ids_only():
    slot_id = build_slot_id("Tuesday", "10:00")
    app.store.save_availability(user_id="444444444444444444", selected_slots={slot_id})

    user_map = app.build_heatmap_user_map(["444444444444444444"])

    assert slot_id in user_map
    assert "444444444444444444" in user_map[slot_id]


def test_heatmap_user_facing_copy_does_not_expose_group_a_or_group_b_labels():
    client = TestClient(app.app)

    response = client.get("/heatmap")
    assert response.status_code == 200
    body = response.text
    assert "Group A" not in body
    assert "Group B" not in body

    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    content = app_js.read_text()
    assert "Group A" not in content
    assert "Group B" not in content

    bot_file = Path(__file__).resolve().parents[1] / "agent-of-the-king.py"
    bot_content = bot_file.read_text()
    assert "Group A role members" not in bot_content
    assert "Group B other users with availability" not in bot_content
