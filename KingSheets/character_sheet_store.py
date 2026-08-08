import os
import sqlite3
from pathlib import Path
from typing import Any


class CharacterSheetStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or os.environ.get("AVAILABILITY_DB_PATH", os.path.join(os.path.dirname(__file__), "character_sheets.sqlite"))
        self._initialize()

    def _initialize(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            table_exists = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='character_sheets'"
            ).fetchone() is not None
            if not table_exists:
                connection.execute(
                    """
                    CREATE TABLE character_sheets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        owner_id TEXT NOT NULL,
                        system_name TEXT NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        character_name TEXT,
                        body_html TEXT,
                        pdf_path TEXT,
                        pdf_filename TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            else:
                existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(character_sheets)")}
                if "id" not in existing_columns:
                    connection.execute("ALTER TABLE character_sheets RENAME TO character_sheets_old")
                    connection.execute(
                        """
                        CREATE TABLE character_sheets (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            owner_id TEXT NOT NULL,
                            system_name TEXT NOT NULL,
                            title TEXT NOT NULL DEFAULT '',
                            character_name TEXT,
                            body_html TEXT,
                            pdf_path TEXT,
                            pdf_filename TEXT,
                            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                    connection.execute(
                        """
                        INSERT INTO character_sheets (owner_id, system_name, title, character_name, body_html, pdf_path, pdf_filename, created_at, updated_at)
                        SELECT owner_id, system_name, title, character_name, body_html, pdf_path, pdf_filename, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        FROM character_sheets_old
                        """
                    )
                    connection.execute("DROP TABLE character_sheets_old")
                    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(character_sheets)")}

                if "title" not in existing_columns:
                    connection.execute("ALTER TABLE character_sheets ADD COLUMN title TEXT NOT NULL DEFAULT ''")
                if "character_name" not in existing_columns:
                    connection.execute("ALTER TABLE character_sheets ADD COLUMN character_name TEXT")
                if "created_at" not in existing_columns:
                    connection.execute("ALTER TABLE character_sheets ADD COLUMN created_at TEXT")
                if "updated_at" not in existing_columns:
                    connection.execute("ALTER TABLE character_sheets ADD COLUMN updated_at TEXT")

            connection.execute("UPDATE character_sheets SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
            connection.execute("UPDATE character_sheets SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_character_sheets_owner_system ON character_sheets(owner_id, system_name)")
            connection.commit()
        finally:
            connection.close()

    def upsert_character_sheet(
        self,
        owner_id: str,
        system_name: str,
        title: str,
        character_name: str | None = None,
        body_html: str | None = None,
        pdf_path: str | None = None,
        pdf_filename: str | None = None,
        sheet_id: str | int | None = None,
        create_new: bool = False,
    ) -> None:
        cleaned_owner_id = str(owner_id).strip()
        cleaned_system_name = str(system_name).strip()
        cleaned_title = str(title).strip()
        cleaned_character_name = str(character_name).strip() if character_name is not None else None
        if not cleaned_owner_id or not cleaned_system_name:
            return

        connection = sqlite3.connect(self.db_path)
        try:
            target_row_id = None
            if sheet_id is not None and str(sheet_id).strip() != "":
                try:
                    target_row_id = int(str(sheet_id).strip())
                except ValueError:
                    target_row_id = None

            if create_new:
                connection.execute(
                    """
                    INSERT INTO character_sheets (owner_id, system_name, title, character_name, body_html, pdf_path, pdf_filename, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        cleaned_owner_id,
                        cleaned_system_name,
                        cleaned_title,
                        cleaned_character_name,
                        body_html,
                        pdf_path,
                        pdf_filename,
                    ),
                )
            else:
                existing_row = None
                if target_row_id is not None:
                    existing_row = connection.execute(
                        "SELECT id FROM character_sheets WHERE id = ?",
                        (target_row_id,),
                    ).fetchone()
                if existing_row is None:
                    existing_row = connection.execute(
                        "SELECT id FROM character_sheets WHERE owner_id = ? AND system_name = ? ORDER BY id ASC LIMIT 1",
                        (cleaned_owner_id, cleaned_system_name),
                    ).fetchone()

                if existing_row is None:
                    connection.execute(
                        """
                        INSERT INTO character_sheets (owner_id, system_name, title, character_name, body_html, pdf_path, pdf_filename, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """,
                        (
                            cleaned_owner_id,
                            cleaned_system_name,
                            cleaned_title,
                            cleaned_character_name,
                            body_html,
                            pdf_path,
                            pdf_filename,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE character_sheets
                        SET title = ?,
                            character_name = ?,
                            body_html = ?,
                            pdf_path = ?,
                            pdf_filename = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            cleaned_title,
                            cleaned_character_name,
                            body_html,
                            pdf_path,
                            pdf_filename,
                            existing_row[0],
                        ),
                    )
            connection.commit()
        finally:
            connection.close()

    def delete_character_sheet(self, owner_id: str, sheet_id: str | int | None = None) -> bool:
        cleaned_owner_id = str(owner_id).strip()
        if not cleaned_owner_id or sheet_id is None:
            return False

        connection = sqlite3.connect(self.db_path)
        try:
            try:
                normalized_sheet_id = int(str(sheet_id).strip())
            except ValueError:
                return False

            row = connection.execute(
                "SELECT id, pdf_path FROM character_sheets WHERE owner_id = ? AND id = ?",
                (cleaned_owner_id, normalized_sheet_id),
            ).fetchone()
            if not row:
                return False

            pdf_path = row[1]
            connection.execute(
                "DELETE FROM character_sheets WHERE owner_id = ? AND id = ?",
                (cleaned_owner_id, normalized_sheet_id),
            )
            connection.commit()

            if pdf_path:
                try:
                    Path(pdf_path).unlink(missing_ok=True)
                except OSError:
                    pass
            return True
        finally:
            connection.close()

    def list_character_sheets(self, owner_id: str) -> list[dict[str, Any]]:
        cleaned_owner_id = str(owner_id).strip()
        if not cleaned_owner_id:
            return []

        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT id, owner_id, system_name, title, character_name, body_html, pdf_path, pdf_filename, created_at, updated_at
                FROM character_sheets
                WHERE owner_id = ?
                ORDER BY updated_at DESC, system_name ASC, id ASC
                """,
                (cleaned_owner_id,),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "owner_id": row[1],
                    "system_name": row[2],
                    "title": row[3],
                    "character_name": row[4],
                    "body_html": row[5] or "",
                    "pdf_path": row[6],
                    "pdf_filename": row[7],
                    "created_at": row[8],
                    "updated_at": row[9],
                }
                for row in rows
            ]
        finally:
            connection.close()

    def load_character_sheet(self, owner_id: str, system_name: str, sheet_id: str | int | None = None) -> dict[str, Any] | None:
        cleaned_owner_id = str(owner_id).strip()
        cleaned_system_name = str(system_name).strip()
        if not cleaned_owner_id or not cleaned_system_name:
            return None

        connection = sqlite3.connect(self.db_path)
        try:
            target_row_id = None
            if sheet_id is not None and str(sheet_id).strip() != "":
                try:
                    target_row_id = int(str(sheet_id).strip())
                except ValueError:
                    target_row_id = None

            if target_row_id is not None:
                row = connection.execute(
                    """
                    SELECT id, owner_id, system_name, title, character_name, body_html, pdf_path, pdf_filename, created_at, updated_at
                    FROM character_sheets
                    WHERE owner_id = ? AND system_name = ? AND id = ?
                    """,
                    (cleaned_owner_id, cleaned_system_name, target_row_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT id, owner_id, system_name, title, character_name, body_html, pdf_path, pdf_filename, created_at, updated_at
                    FROM character_sheets
                    WHERE owner_id = ? AND system_name = ?
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (cleaned_owner_id, cleaned_system_name),
                ).fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "owner_id": row[1],
                "system_name": row[2],
                "title": row[3],
                "character_name": row[4],
                "body_html": row[5] or "",
                "pdf_path": row[6],
                "pdf_filename": row[7],
                "created_at": row[8],
                "updated_at": row[9],
            }
        finally:
            connection.close()
