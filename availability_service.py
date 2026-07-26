import os
import sqlite3
import json
import uuid
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


def normalize_game_name(name: str) -> str:
    return " ".join(name.strip().split()).lower()


def display_game_name(name: str) -> str:
    return " ".join(name.strip().split())


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

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS games (
                    name TEXT NOT NULL,
                    game_type TEXT,
                    session_length_hours REAL NOT NULL DEFAULT 4,
                    normalized_name TEXT NOT NULL UNIQUE,
                    role_id TEXT,
                    role_name TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS heatmap_contexts (
                    context_id TEXT NOT NULL PRIMARY KEY,
                    game_name TEXT NOT NULL,
                    session_length_hours REAL NOT NULL DEFAULT 4,
                    guild_id TEXT,
                    role_id TEXT,
                    participant_user_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            game_columns = {row[1] for row in connection.execute("PRAGMA table_info(games)")}
            if "game_type" not in game_columns:
                connection.execute("ALTER TABLE games ADD COLUMN game_type TEXT")
            if "session_length_hours" not in game_columns:
                connection.execute("ALTER TABLE games ADD COLUMN session_length_hours REAL NOT NULL DEFAULT 4")
            connection.execute(
                "UPDATE games SET session_length_hours = 4 WHERE session_length_hours IS NULL OR session_length_hours <= 0"
            )
            if "role_id" not in game_columns:
                connection.execute("ALTER TABLE games ADD COLUMN role_id TEXT")
            if "role_name" not in game_columns:
                connection.execute("ALTER TABLE games ADD COLUMN role_name TEXT")

            context_columns = {row[1] for row in connection.execute("PRAGMA table_info(heatmap_contexts)")}
            if "session_length_hours" not in context_columns:
                connection.execute(
                    "ALTER TABLE heatmap_contexts ADD COLUMN session_length_hours REAL NOT NULL DEFAULT 4"
                )
            if "guild_id" not in context_columns:
                connection.execute("ALTER TABLE heatmap_contexts ADD COLUMN guild_id TEXT")
            if "role_id" not in context_columns:
                connection.execute("ALTER TABLE heatmap_contexts ADD COLUMN role_id TEXT")
            connection.execute(
                "UPDATE heatmap_contexts SET session_length_hours = 4 WHERE session_length_hours IS NULL OR session_length_hours <= 0"
            )
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

    def list_user_ids(self) -> list[str]:
        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT DISTINCT user_id FROM availability ORDER BY user_id"
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            connection.close()

    def add_game(
        self,
        name: str,
        game_type: str,
        session_length_hours: float,
        role_id: str,
        role_name: str,
    ) -> str:
        cleaned_name = display_game_name(name)
        normalized_name = normalize_game_name(cleaned_name)
        cleaned_game_type = display_game_name(game_type)
        if not normalized_name:
            raise ValueError("Game name cannot be empty")
        if not cleaned_game_type:
            raise ValueError("Game type cannot be empty")
        if session_length_hours <= 0:
            raise ValueError("Session length must be greater than 0")
        if not role_id:
            raise ValueError("Role is required")
        cleaned_role_name = display_game_name(role_name)

        connection = sqlite3.connect(self.db_path)
        try:
            existing = connection.execute(
                "SELECT active, game_type, session_length_hours, role_id, role_name FROM games WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()
            if not existing:
                connection.execute(
                    "INSERT INTO games (name, game_type, session_length_hours, normalized_name, role_id, role_name, active) VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (
                        cleaned_name,
                        cleaned_game_type,
                        session_length_hours,
                        normalized_name,
                        role_id,
                        cleaned_role_name,
                    ),
                )
                connection.commit()
                return "created"

            is_active = bool(existing[0])
            existing_game_type = existing[1] if len(existing) > 1 else None
            existing_session_length_hours = float(existing[2] if len(existing) > 2 and existing[2] else 4)
            existing_role_id = existing[3] if len(existing) > 3 else None
            existing_role_name = existing[4] if len(existing) > 4 else None
            if is_active:
                if (
                    existing_game_type == cleaned_game_type
                    and abs(existing_session_length_hours - session_length_hours) < 1e-9
                    and existing_role_id == role_id
                    and existing_role_name == cleaned_role_name
                    and existing[0]
                ):
                    return "already_active"
                connection.execute(
                    "UPDATE games SET name = ?, game_type = ?, session_length_hours = ?, role_id = ?, role_name = ? WHERE normalized_name = ?",
                    (
                        cleaned_name,
                        cleaned_game_type,
                        session_length_hours,
                        role_id,
                        cleaned_role_name,
                        normalized_name,
                    ),
                )
                connection.commit()
                return "updated"

            connection.execute(
                "UPDATE games SET name = ?, game_type = ?, session_length_hours = ?, role_id = ?, role_name = ?, active = 1 WHERE normalized_name = ?",
                (
                    cleaned_name,
                    cleaned_game_type,
                    session_length_hours,
                    role_id,
                    cleaned_role_name,
                    normalized_name,
                ),
            )
            connection.commit()
            return "reactivated"
        finally:
            connection.close()

    def remove_game(self, name: str) -> str:
        normalized_name = normalize_game_name(name)
        if not normalized_name:
            raise ValueError("Game name cannot be empty")

        connection = sqlite3.connect(self.db_path)
        try:
            existing = connection.execute(
                "SELECT active FROM games WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()
            if not existing:
                return "not_found"

            is_active = bool(existing[0])
            if not is_active:
                return "already_inactive"

            connection.execute(
                "UPDATE games SET active = 0 WHERE normalized_name = ?",
                (normalized_name,),
            )
            connection.commit()
            return "deactivated"
        finally:
            connection.close()

    def list_games(self, active_only: bool = True) -> list[dict[str, str | None]]:
        connection = sqlite3.connect(self.db_path)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(games)")}
            has_role_columns = "role_id" in columns and "role_name" in columns
            has_game_type_column = "game_type" in columns
            has_session_length_column = "session_length_hours" in columns
            if active_only:
                query = (
                    "SELECT name, game_type, session_length_hours, role_id, role_name "
                    "FROM games WHERE active = 1 ORDER BY name COLLATE NOCASE"
                )
            else:
                query = (
                    "SELECT name, game_type, session_length_hours, role_id, role_name "
                    "FROM games ORDER BY name COLLATE NOCASE"
                )

            if has_role_columns and has_game_type_column and has_session_length_column:
                rows = connection.execute(query).fetchall()
                return [
                    {
                        "name": row[0],
                        "game_type": row[1],
                        "session_length_hours": float(row[2] if row[2] else 4),
                        "role_id": row[3],
                        "role_name": row[4],
                    }
                    for row in rows
                ]

            # Backward-compatibility for very old schemas before game_type/role columns exist.
            if active_only:
                rows = connection.execute(
                    "SELECT name FROM games WHERE active = 1 ORDER BY name COLLATE NOCASE"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT name FROM games ORDER BY name COLLATE NOCASE"
                ).fetchall()
            return [
                {
                    "name": row[0],
                    "game_type": None,
                    "session_length_hours": 4.0,
                    "role_id": None,
                    "role_name": None,
                }
                for row in rows
            ]
        finally:
            connection.close()

    def update_game(
        self,
        current_name: str,
        new_name: str,
        game_type: str,
        session_length_hours: float,
        role_id: str,
        role_name: str,
    ) -> str:
        current_normalized = normalize_game_name(current_name)
        next_name = display_game_name(new_name)
        next_normalized = normalize_game_name(next_name)
        next_game_type = display_game_name(game_type)
        next_role_name = display_game_name(role_name)

        if not current_normalized:
            raise ValueError("Current game name cannot be empty")
        if not next_normalized:
            raise ValueError("Game name cannot be empty")
        if not next_game_type:
            raise ValueError("Game type cannot be empty")
        if session_length_hours <= 0:
            raise ValueError("Session length must be greater than 0")
        if not role_id:
            raise ValueError("Role is required")

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT normalized_name FROM games WHERE normalized_name = ?",
                (current_normalized,),
            ).fetchone()
            if not row:
                return "not_found"

            if next_normalized != current_normalized:
                conflict = connection.execute(
                    "SELECT 1 FROM games WHERE normalized_name = ?",
                    (next_normalized,),
                ).fetchone()
                if conflict:
                    return "name_conflict"

            connection.execute(
                """
                UPDATE games
                SET name = ?,
                    game_type = ?,
                    session_length_hours = ?,
                    normalized_name = ?,
                    role_id = ?,
                    role_name = ?
                WHERE normalized_name = ?
                """,
                (
                    next_name,
                    next_game_type,
                    session_length_hours,
                    next_normalized,
                    role_id,
                    next_role_name,
                    current_normalized,
                ),
            )
            connection.commit()
            return "updated"
        finally:
            connection.close()

    def create_heatmap_context(
        self,
        game_name: str,
        participant_user_ids: list[str],
        session_length_hours: float,
        guild_id: str | None = None,
        role_id: str | None = None,
    ) -> str:
        context_id = str(uuid.uuid4())
        cleaned_game_name = display_game_name(game_name)
        participant_ids = sorted({display_game_name(user_id) for user_id in participant_user_ids if user_id.strip()})
        payload = json.dumps(participant_ids)
        effective_session_length_hours = session_length_hours if session_length_hours > 0 else 4.0

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                INSERT INTO heatmap_contexts (
                    context_id,
                    game_name,
                    session_length_hours,
                    guild_id,
                    role_id,
                    participant_user_ids_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    context_id,
                    cleaned_game_name,
                    effective_session_length_hours,
                    guild_id,
                    role_id,
                    payload,
                ),
            )
            connection.commit()
            return context_id
        finally:
            connection.close()

    def get_heatmap_context(self, context_id: str) -> dict[str, object] | None:
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT context_id, game_name, session_length_hours, guild_id, role_id, participant_user_ids_json
                FROM heatmap_contexts
                WHERE context_id = ?
                """,
                (context_id,),
            ).fetchone()
            if not row:
                return None

            participants = json.loads(row[5]) if row[5] else []
            if not isinstance(participants, list):
                participants = []
            return {
                "context_id": row[0],
                "game_name": row[1],
                "session_length_hours": float(row[2] if row[2] else 4.0),
                "guild_id": row[3],
                "role_id": row[4],
                "participant_user_ids": [str(value) for value in participants],
            }
        finally:
            connection.close()
