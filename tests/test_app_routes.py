from pathlib import Path
import asyncio

from fastapi.testclient import TestClient

import app
from availability_service import build_slot_id


def test_format_timestamp_converts_naive_utc_values_to_eastern_time():
    assert app.format_timestamp("2026-08-08 06:18:00") == "08/08/2026 02:18 AM"


def test_home_page_uses_query_params_and_marks_user_readonly():
    client = TestClient(app.app)

    response = client.get("/", params={"user_id": "discord-123"})

    assert response.status_code == 200
    body = response.text
    assert 'name="user_id"' in body
    assert 'readonly' in body
    assert 'value="discord-123"' in body


def test_king_sheets_dashboard_lists_saved_sheets_for_the_current_user():
    client = TestClient(app.app)
    app.character_sheet_store.upsert_character_sheet(
        owner_id="sheet-owner",
        system_name="World of Darkness Mortals",
        title="A Test Sheet",
        body_html="<p>Notes here</p>",
    )

    response = client.get("/king-sheets", params={"user_id": "sheet-owner"})

    assert response.status_code == 200
    body = response.text
    assert "Character Sheets" in body
    assert 'aria-label="Create a new sheet"' in body
    assert 'fa-solid fa-plus' in body
    assert 'aria-label="Open guided character creator"' in body
    assert '/king-sheets/wizard?user_id=sheet-owner' in body
    assert '/king-sheets/guided' not in body
    assert "World of Darkness Mortals" in body


def test_king_sheets_dashboard_renders_download_modal_for_available_systems():
    client = TestClient(app.app)

    response = client.get("/king-sheets", params={"user_id": "sheet-owner"})

    assert response.status_code == 200
    body = response.text
    assert "Download an editable pdf of an available system" in body
    assert 'id="modal_download_system"' in body
    assert "World of Darkness Mortals" in body


def test_uploading_a_pdf_persists_the_file_and_renders_a_download_link():
    client = TestClient(app.app)

    response = client.post(
        "/king-sheets",
        data={
            "user_id": "upload-owner",
            "system_name": "World of Darkness Mortals",
            "title": "Uploaded Sheet",
            "character_name": "Alice",
            "body_html": "<p>Uploaded</p>",
        },
        files={"pdf_file": ("uploaded-sheet.pdf", b"%PDF-1.4\n", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    sheet = app.character_sheet_store.load_character_sheet("upload-owner", "World of Darkness Mortals")
    assert sheet is not None
    assert sheet["pdf_filename"] == "uploaded-sheet.pdf"
    assert sheet["pdf_path"] is not None

    dashboard_response = client.get("/king-sheets", params={"user_id": "upload-owner"})
    assert dashboard_response.status_code == 200
    assert "/uploads/upload-owner_uploaded-sheet.pdf" in dashboard_response.text


def test_uploading_a_pdf_with_spaces_in_the_filename_renders_a_url_safe_download_link():
    client = TestClient(app.app)

    response = client.post(
        "/king-sheets",
        data={
            "user_id": "space-name-owner",
            "system_name": "World of Darkness Mortals",
            "character_name": "Alice",
            "body_html": "<p>Uploaded</p>",
        },
        files={"pdf_file": ("Bobby Drop.pdf", b"%PDF-1.4\n", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303

    dashboard_response = client.get("/king-sheets", params={"user_id": "space-name-owner"})
    assert dashboard_response.status_code == 200
    assert "%20" in dashboard_response.text


def test_modal_upload_submission_returns_json_redirect_for_ajax_style_requests():
    client = TestClient(app.app)

    response = client.post(
        "/king-sheets",
        data={
            "user_id": "modal-ajax-owner",
            "system_name": "World of Darkness Mortals",
            "create_new": "true",
            "character_name": "",
            "body_html": "",
            "submitted_via_ajax": "true",
        },
        files={"pdf_file": ("modal-upload.pdf", b"%PDF-1.4\n", "application/pdf")},
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["redirect_url"].endswith("/king-sheets?user_id=modal-ajax-owner")


def test_uploading_a_new_pdf_replaces_the_existing_stored_pdf_for_a_sheet():
    client = TestClient(app.app)
    app.character_sheet_store.upsert_character_sheet(
        owner_id="replace-pdf-owner",
        system_name="World of Darkness Mortals",
        title="",
        character_name="Alice",
        body_html="<p>Original</p>",
        pdf_path=str(app.KING_SHEETS_DIR / "replace-pdf-owner_original.pdf"),
        pdf_filename="original.pdf",
    )
    original_path = Path(app.KING_SHEETS_DIR / "replace-pdf-owner_original.pdf")
    original_path.write_bytes(b"old-pdf")

    response = client.post(
        "/king-sheets",
        data={
            "user_id": "replace-pdf-owner",
            "system_name": "World of Darkness Mortals",
            "character_name": "Alice",
            "body_html": "<p>Updated</p>",
        },
        files={"pdf_file": ("replacement.pdf", b"new-pdf", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    sheet = app.character_sheet_store.load_character_sheet("replace-pdf-owner", "World of Darkness Mortals")
    assert sheet is not None
    assert sheet["pdf_filename"] == "replacement.pdf"
    assert sheet["pdf_path"] == str(app.UPLOADS_DIR / "replace-pdf-owner_replacement.pdf")
    assert not original_path.exists()


def test_uploading_a_new_pdf_preserves_existing_character_and_notes_when_blank_values_are_submitted():
    client = TestClient(app.app)
    app.character_sheet_store.upsert_character_sheet(
        owner_id="blank-values-owner",
        system_name="World of Darkness Mortals",
        title="",
        character_name="Alice",
        body_html="<p>Original notes</p>",
        pdf_path=str(app.KING_SHEETS_DIR / "blank-values-owner_original.pdf"),
        pdf_filename="original.pdf",
    )

    response = client.post(
        "/king-sheets",
        data={
            "user_id": "blank-values-owner",
            "system_name": "World of Darkness Mortals",
            "character_name": "",
            "body_html": "",
        },
        files={"pdf_file": ("replacement.pdf", b"new-pdf", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    sheet = app.character_sheet_store.load_character_sheet("blank-values-owner", "World of Darkness Mortals")
    assert sheet is not None
    assert sheet["character_name"] == "Alice"
    assert sheet["body_html"] == "<p>Original notes</p>"


def test_creating_multiple_sheets_for_the_same_system_keeps_them_separate():
    client = TestClient(app.app)
    owner_id = "multi-sheet-owner-isolated"

    import sqlite3

    conn = sqlite3.connect(app.character_sheet_store.db_path)
    try:
        conn.execute("DELETE FROM character_sheets WHERE owner_id = ?", (owner_id,))
        conn.commit()
    finally:
        conn.close()

    for character_name, body_html in [("Alice", "<p>First</p>"), ("Bob", "<p>Second</p>")]:
        response = client.post(
            "/king-sheets",
            data={
                "user_id": owner_id,
                "system_name": "World of Darkness Mortals",
                "character_name": character_name,
                "body_html": body_html,
                "create_new": "true",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

    sheets = app.character_sheet_store.list_character_sheets(owner_id)
    assert len(sheets) == 2
    assert {sheet["character_name"] for sheet in sheets} == {"Alice", "Bob"}


def test_save_king_sheet_persists_a_new_sheet_and_redirects_back_to_dashboard():
    client = TestClient(app.app)

    response = client.post(
        "/king-sheets",
        data={
            "user_id": "new-sheet-owner",
            "system_name": "World of Darkness Mortals",
            "title": "My New Sheet",
            "body_html": "<p>Ready to play</p>",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    sheet = app.character_sheet_store.load_character_sheet("new-sheet-owner", "World of Darkness Mortals")
    assert sheet is not None
    assert sheet["title"] == ""


def test_king_sheets_dashboard_shows_delete_confirmation_modal():
    client = TestClient(app.app)

    response = client.get("/king-sheets", params={"user_id": "delete-confirm-owner"})

    assert response.status_code == 200
    body = response.text
    assert 'id="delete-sheet-modal"' in body
    assert 'Delete sheet?' in body
    assert 'This action permanently deletes the selected sheet' in body


def test_delete_king_sheet_removes_the_sheet_and_redirects_back_to_dashboard():
    client = TestClient(app.app)
    app.character_sheet_store.upsert_character_sheet(
        owner_id="delete-route-owner",
        system_name="World of Darkness Mortals",
        title="Delete Me",
        character_name="Dora",
        body_html="<p>Remove</p>",
    )
    sheet = app.character_sheet_store.load_character_sheet("delete-route-owner", "World of Darkness Mortals")
    assert sheet is not None

    response = client.post(
        "/king-sheets/delete",
        data={"user_id": "delete-route-owner", "sheet_id": sheet["id"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert app.character_sheet_store.list_character_sheets("delete-route-owner") == []


def test_king_sheets_dashboard_shows_saved_count_and_disables_create_at_ten_sheets():
    client = TestClient(app.app)
    owner_id = "sheet-limit-owner"
    for index in range(10):
        app.character_sheet_store.upsert_character_sheet(
            owner_id=owner_id,
            system_name=f"System {index}",
            title=f"Sheet {index}",
            body_html="",
        )

    response = client.get("/king-sheets", params={"user_id": owner_id})

    assert response.status_code == 200
    body = response.text
    assert "10 saved / 10" in body
    assert 'disabled' in body
    assert 'aria-label="Create a new sheet"' in body
    assert 'fa-solid fa-plus' in body


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


def test_heatmap_live_lookup_uses_context_name_when_live_name_missing(monkeypatch):
    app.store.save_availability(
        user_id="666666666666666666",
        selected_slots={build_slot_id("Thursday", "18:00")},
    )

    async def fake_fetch_role_members(guild_id: str, role_id: str):
        return [
            {
                "id": "666666666666666666",
                "name": "",
            }
        ]

    monkeypatch.setattr(app, "fetch_role_members", fake_fetch_role_members)

    users, _, _ = asyncio.run(
        app.build_heatmap_users(
            {
                "game_name": "Kings Game",
                "session_length_hours": 4.0,
                "guild_id": "1",
                "role_id": "2",
                "participant_user_ids": ["666666666666666666"],
                "participant_users": [
                    {"id": "666666666666666666", "name": "Alice Smith"},
                ],
            }
        )
    )

    group_a = [user for user in users if user["group"] == "A"]
    assert len(group_a) == 1
    assert group_a[0]["id"] == "666666666666666666"
    assert group_a[0]["name"] == "Alice Smith"


def test_heatmap_partial_live_fetch_keeps_expected_participant_in_group_a(monkeypatch):
    app.store.save_availability(
        user_id="777777777777777777",
        selected_slots={build_slot_id("Friday", "19:00")},
    )

    async def fake_fetch_role_members(guild_id: str, role_id: str):
        return [
            {
                "id": "888888888888888888",
                "name": "Bob Jones",
            }
        ]

    monkeypatch.setattr(app, "fetch_role_members", fake_fetch_role_members)

    users, _, _ = asyncio.run(
        app.build_heatmap_users(
            {
                "game_name": "Kings Game",
                "session_length_hours": 4.0,
                "guild_id": "1",
                "role_id": "2",
                "participant_user_ids": ["888888888888888888", "777777777777777777"],
                "participant_users": [
                    {"id": "888888888888888888", "name": "Bob Jones"},
                    {"id": "777777777777777777", "name": "Carol Miles"},
                ],
            }
        )
    )

    participant_user = next((user for user in users if user["id"] == "777777777777777777"), None)
    assert participant_user is not None
    assert participant_user["group"] == "A"
    assert participant_user["name"] == "Carol Miles"


def test_heatmap_group_b_uses_persisted_profile_name_when_context_lacks_name(monkeypatch):
    app.store.save_availability(
        user_id="889999999999999999",
        selected_slots={build_slot_id("Friday", "19:00")},
    )
    app.store.save_availability(
        user_id="888888888888888888",
        selected_slots={build_slot_id("Friday", "19:30")},
    )
    app.store.upsert_user_profile("889999999999999999", "Persisted Carol", source="test")

    async def fake_fetch_role_members(guild_id: str, role_id: str):
        return [{"id": "888888888888888888", "name": "Bob Jones"}]

    monkeypatch.setattr(app, "fetch_role_members", fake_fetch_role_members)

    users, _, _ = asyncio.run(
        app.build_heatmap_users(
            {
                "game_name": "Kings Game",
                "session_length_hours": 4.0,
                "guild_id": "1",
                "role_id": "2",
                "participant_user_ids": ["888888888888888888"],
                "participant_users": [
                    {"id": "888888888888888888", "name": "Bob Jones"},
                ],
            }
        )
    )

    group_b_user = next((user for user in users if user["id"] == "889999999999999999"), None)
    assert group_b_user is not None
    assert group_b_user["group"] == "B"
    assert group_b_user["name"] == "Persisted Carol"


def test_heatmap_without_context_uses_persisted_profile_name():
    app.store.save_availability(
        user_id="887777777777777777",
        selected_slots={build_slot_id("Sunday", "08:00")},
    )
    app.store.upsert_user_profile("887777777777777777", "Persisted Dana", source="test")

    users, _, _ = asyncio.run(app.build_heatmap_users(None))

    saved_user = next((user for user in users if user["id"] == "887777777777777777"), None)
    assert saved_user is not None
    assert saved_user["name"] == "Persisted Dana"


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


