import os
import sqlite3
import tempfile

from availability_service import AvailabilityStore, build_slot_id, build_time_slots


def test_build_time_slots_produces_48_half_hour_slots():
    slots = build_time_slots()
    assert len(slots) == 48
    assert slots[0] == "00:00"
    assert slots[-1] == "23:30"


def test_save_and_load_availability_round_trip():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        selected = {build_slot_id("Monday", "19:00"), build_slot_id("Wednesday", "21:30")}
        store.save_availability(user_id="user-1", selected_slots=selected)

        reloaded = store.load_availability(user_id="user-1")
        assert reloaded == selected


def test_save_replaces_old_slots_for_same_user():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        store.save_availability(user_id="user-2", selected_slots={build_slot_id("Monday", "09:00")})
        store.save_availability(user_id="user-2", selected_slots={build_slot_id("Friday", "16:30")})

        reloaded = store.load_availability(user_id="user-2")
        assert reloaded == {build_slot_id("Friday", "16:30")}


def test_get_slot_counts_returns_usage_per_slot():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        store.save_availability(user_id="user-1", selected_slots={build_slot_id("Monday", "09:00")})
        store.save_availability(user_id="user-2", selected_slots={build_slot_id("Monday", "09:00"), build_slot_id("Friday", "16:30")})

        counts = store.get_slot_counts()
        assert counts[build_slot_id("Monday", "09:00")] == 2
        assert counts[build_slot_id("Friday", "16:30")] == 1


def test_list_user_ids_returns_distinct_sorted_user_ids():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        store.save_availability(user_id="user-b", selected_slots={build_slot_id("Monday", "09:00")})
        store.save_availability(user_id="user-a", selected_slots={build_slot_id("Tuesday", "10:00")})

        assert store.list_user_ids() == ["user-a", "user-b"]


def test_add_game_enforces_case_insensitive_uniqueness():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        assert store.add_game("Arkham Horror", "Arkham Horror LCG", 4.0, role_id="1", role_name="Investigators") == "created"
        assert store.add_game("arkham horror", "Arkham Horror LCG", 4.0, role_id="1", role_name="Investigators") == "already_active"
        assert store.list_games() == [{
            "name": "Arkham Horror",
            "game_type": "Arkham Horror LCG",
            "session_length_hours": 4.0,
            "role_id": "1",
            "role_name": "Investigators",
        }]


def test_remove_game_marks_game_inactive_and_hides_from_active_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        store.add_game("Marvel Champions", "Marvel", 3.5, role_id="2", role_name="Champions")
        assert store.remove_game("marvel champions") == "deactivated"
        assert store.list_games(active_only=True) == []
        assert store.list_games(active_only=False) == [
            {
                "name": "Marvel Champions",
                "game_type": "Marvel",
                "session_length_hours": 3.5,
                "role_id": "2",
                "role_name": "Champions",
            }
        ]


def test_add_game_reactivates_inactive_game():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        store.add_game("Spirit Island", "Board Game", 4.0, role_id="3", role_name="Island Players")
        store.remove_game("Spirit Island")
        assert store.add_game("spirit island", "Co-op Board Game", 5.0, role_id="4", role_name="Island Team") == "reactivated"
        assert store.list_games() == [{
            "name": "spirit island",
            "game_type": "Co-op Board Game",
            "session_length_hours": 5.0,
            "role_id": "4",
            "role_name": "Island Team",
        }]


def test_add_game_updates_role_when_already_active():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        store.add_game("Root", "Board Game", 4.0, role_id="5", role_name="Old Role")
        assert store.add_game("Root", "Strategy Board Game", 6.0, role_id="6", role_name="New Role") == "updated"
        assert store.list_games() == [{
            "name": "Root",
            "game_type": "Strategy Board Game",
            "session_length_hours": 6.0,
            "role_id": "6",
            "role_name": "New Role",
        }]


def test_update_game_changes_name_type_and_role():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        store.add_game("Arkham Campaign", "Arkham Horror LCG", 4.0, role_id="10", role_name="Old Role")
        result = store.update_game(
            current_name="Arkham Campaign",
            new_name="Arkham Campaign Night 2",
            game_type="Arkham Horror LCG",
            session_length_hours=3.0,
            role_id="11",
            role_name="New Role",
        )

        assert result == "updated"
        assert store.list_games() == [{
            "name": "Arkham Campaign Night 2",
            "game_type": "Arkham Horror LCG",
            "session_length_hours": 3.0,
            "role_id": "11",
            "role_name": "New Role",
        }]


def test_update_game_rejects_name_conflict():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        store.add_game("Game One", "Type A", 4.0, role_id="1", role_name="Role A")
        store.add_game("Game Two", "Type B", 4.0, role_id="2", role_name="Role B")

        result = store.update_game(
            current_name="Game Two",
            new_name="Game One",
            game_type="Type B",
            session_length_hours=4.0,
            role_id="2",
            role_name="Role B",
        )

        assert result == "name_conflict"


def test_update_game_returns_not_found_for_missing_game():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        result = store.update_game(
            current_name="Missing Game",
            new_name="Updated Name",
            game_type="Type",
            session_length_hours=4.0,
            role_id="1",
            role_name="Role",
        )

        assert result == "not_found"


def test_heatmap_context_round_trip():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        context_id = store.create_heatmap_context(
            game_name="Hotel California",
            participant_user_ids=["alice", "bob"],
            session_length_hours=5.5,
            guild_id="123",
            role_id="456",
        )
        loaded = store.get_heatmap_context(context_id)

        assert loaded is not None
        assert loaded["game_name"] == "Hotel California"
        assert loaded["session_length_hours"] == 5.5
        assert loaded["guild_id"] == "123"
        assert loaded["role_id"] == "456"
        assert loaded["participant_user_ids"] == ["alice", "bob"]


def test_heatmap_context_missing_id_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "availability.sqlite")
        store = AvailabilityStore(db_path=db_path)

        assert store.get_heatmap_context("does-not-exist") is None
