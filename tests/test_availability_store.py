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
