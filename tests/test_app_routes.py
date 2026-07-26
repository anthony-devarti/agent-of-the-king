from pathlib import Path

from fastapi.testclient import TestClient

import app


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
        data={"user_id": "discord-123", "selected_slots": "Monday:09:00,Wednesday:20:30"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "saved": 2}


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

    response = client.get("/heatmap")

    assert response.status_code == 200
    body = response.text
    assert 'class="user-toggle"' in body
    assert 'id="user-availability-data"' in body


def test_heatmap_renders_live_recommendation_summary():
    client = TestClient(app.app)

    response = client.get("/heatmap")

    assert response.status_code == 200
    body = response.text
    assert 'id="recommendation-summary"' in body
    assert 'data-time-slots=' in body


def test_heatmap_renders_user_lock_controls():
    client = TestClient(app.app)

    response = client.get("/heatmap")

    assert response.status_code == 200
    body = response.text
    assert 'class="user-lock-toggle"' in body
    assert 'user-lock-icon' in body


def test_heatmap_recommendation_logic_supports_locked_users():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    content = app_js.read_text()

    assert 'userLockState' in content
    assert 'lockedUserIds' in content
    assert 'is-locked' in content


def test_heatmap_recommendation_copy_mentions_full_four_hour_block():
    app_js = Path(__file__).resolve().parents[1] / "static" / "app.js"
    content = app_js.read_text()

    assert 'full 4-hour block' in content
