import os
import sqlite3
from typing import Dict, Set

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def build_time_slots() -> list[str]:
    slots: list[str] = []
    for hour in range(24):
        for minute in (0, 30):
            slots.append(f"{hour:02d}:{minute:02d}")
    return slots


def build_slot_id(day: str, time_slot: str) -> str:
    return f"{day}:{time_slot}"


class AvailabilityStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("AVAILABILITY_DB_PATH", "availability.sqlite")
        self._initialize()

    def _initialize(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            table_exists = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='availability'"
            ).fetchone()
            if not table_exists:
                connection.execute(
                    """
                    CREATE TABLE availability (
                        user_id TEXT NOT NULL,
                        slot_id TEXT NOT NULL,
                        PRIMARY KEY (user_id, slot_id)
                    )
                    """
                )
            else:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(availability)")}
                if "game_name" in columns:
                    connection.execute("ALTER TABLE availability RENAME TO availability_legacy")
                    connection.execute(
                        """
                        CREATE TABLE availability (
                            user_id TEXT NOT NULL,
                            slot_id TEXT NOT NULL,
                            PRIMARY KEY (user_id, slot_id)
                        )
                        """
                    )
                    connection.execute(
                        "INSERT INTO availability (user_id, slot_id) SELECT user_id, slot_id FROM availability_legacy"
                    )
                    connection.execute("DROP TABLE availability_legacy")
            connection.commit()
        finally:
            connection.close()

    def save_availability(self, user_id: str, selected_slots: Set[str]) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DELETE FROM availability WHERE user_id = ?", (user_id,))
            if selected_slots:
                connection.executemany(
                    "INSERT INTO availability (user_id, slot_id) VALUES (?, ?)",
                    [(user_id, slot_id) for slot_id in sorted(selected_slots)],
                )
            connection.commit()
        finally:
            connection.close()

    def load_availability(self, user_id: str) -> Set[str]:
        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT slot_id FROM availability WHERE user_id = ? ORDER BY slot_id",
                (user_id,),
            ).fetchall()
            return {row[0] for row in rows}
        finally:
            connection.close()

    def get_slot_counts(self) -> Dict[str, int]:
        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT slot_id, COUNT(*) FROM availability GROUP BY slot_id ORDER BY slot_id"
            ).fetchall()
            return {slot_id: count for slot_id, count in rows}
        finally:
            connection.close()
