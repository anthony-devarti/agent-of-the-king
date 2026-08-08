import os
import sqlite3
import tempfile

from KingSheets.character_sheet_store import CharacterSheetStore


def test_character_sheet_store_persists_character_name_and_timestamps():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "character_sheets.sqlite")
        store = CharacterSheetStore(db_path=db_path)

        connection = sqlite3.connect(db_path)
        try:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(character_sheets)")]
        finally:
            connection.close()

        assert "character_name" in columns
        assert "created_at" in columns
        assert "updated_at" in columns

        store.upsert_character_sheet(
            owner_id="owner-1",
            system_name="World of Darkness Mortals",
            title="My Sheet",
            character_name="Alice",
            body_html="<p>Notes</p>",
        )

        sheet = store.load_character_sheet("owner-1", "World of Darkness Mortals")
        assert sheet is not None
        assert sheet["character_name"] == "Alice"
        assert sheet["created_at"]
        assert sheet["updated_at"]


def test_character_sheet_store_backfills_missing_timestamps_for_existing_rows():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "character_sheets.sqlite")
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                """
                CREATE TABLE character_sheets (
                    owner_id TEXT NOT NULL,
                    system_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    character_name TEXT,
                    body_html TEXT,
                    pdf_path TEXT,
                    pdf_filename TEXT,
                    PRIMARY KEY (owner_id, system_name)
                )
                """
            )
            connection.execute(
                "INSERT INTO character_sheets (owner_id, system_name, title) VALUES (?, ?, ?)",
                ("owner-2", "World of Darkness Mortals", "Legacy Sheet"),
            )
            connection.commit()
        finally:
            connection.close()

        store = CharacterSheetStore(db_path=db_path)
        store.upsert_character_sheet(
            owner_id="owner-2",
            system_name="World of Darkness Mortals",
            title="Legacy Sheet",
            body_html="<p>Updated</p>",
        )

        sheet = store.load_character_sheet("owner-2", "World of Darkness Mortals")
        assert sheet is not None
        assert sheet["created_at"]
        assert sheet["updated_at"]


def test_character_sheet_store_can_delete_a_sheet_by_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "character_sheets.sqlite")
        store = CharacterSheetStore(db_path=db_path)

        store.upsert_character_sheet(
            owner_id="owner-3",
            system_name="World of Darkness Mortals",
            title="Delete Me",
            character_name="Charlie",
            body_html="<p>Bye</p>",
        )
        sheet = store.load_character_sheet("owner-3", "World of Darkness Mortals")
        assert sheet is not None

        deleted = store.delete_character_sheet(owner_id="owner-3", sheet_id=sheet["id"])

        assert deleted is True
        assert store.list_character_sheets("owner-3") == []
