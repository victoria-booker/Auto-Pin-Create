"""
SQLite-backed queue of scheduled pins.

Rows are written by routers/schedule.py and posted later by pin_worker.py.
Images are saved to disk at scheduling time because OpenAI download links
expire long before a pin dripped days later gets posted.

Everything lives under PIN_DATA_DIR (the Render persistent disk in
production, ./data locally):
    pin_queue.db
    pin_images/<uuid>.<ext>
"""
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone

STATUS_QUEUED = "queued"
STATUS_POSTING = "posting"
STATUS_POSTED = "posted"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_pins (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        TEXT NOT NULL,
    board_id        TEXT NOT NULL,
    section_id      TEXT,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    link            TEXT NOT NULL,
    alt_text        TEXT,
    image_path      TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    publish_at      TEXT NOT NULL,
    next_attempt_at TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    pin_id          TEXT,
    created_at      TEXT NOT NULL,
    posted_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_pins_due ON scheduled_pins (status, next_attempt_at);
"""


def data_dir():
    # read on every call so tests and Render can point it elsewhere
    return os.environ.get("PIN_DATA_DIR", "data")


def db_path():
    return os.path.join(data_dir(), "pin_queue.db")


def images_dir():
    return os.path.join(data_dir(), "pin_images")


def utc_iso(moment):
    """All timestamps are stored as fixed-format UTC strings so they compare correctly as text."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return utc_iso(datetime.now(timezone.utc))


def _connect():
    os.makedirs(data_dir(), exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def save_image(image_bytes, content_type):
    os.makedirs(images_dir(), exist_ok=True)
    path = os.path.join(images_dir(), uuid.uuid4().hex + _EXTENSIONS.get(content_type, ".img"))
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


def add_pins(rows):
    """rows: dicts with board_id, section_id, title, description, link, alt_text,
    image_path, content_type, publish_at (ISO). Returns the inserted rows."""
    batch_id = uuid.uuid4().hex[:12]
    created_at = now_iso()
    with closing(_connect()) as conn, conn:
        ids = []
        for row in rows:
            cursor = conn.execute(
                """INSERT INTO scheduled_pins
                   (batch_id, board_id, section_id, title, description, link, alt_text,
                    image_path, content_type, publish_at, next_attempt_at, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch_id, row["board_id"], row.get("section_id"), row["title"], row["description"],
                    row["link"], row.get("alt_text"), row["image_path"], row["content_type"],
                    row["publish_at"], row["publish_at"], STATUS_QUEUED, created_at,
                ),
            )
            ids.append(cursor.lastrowid)
    return [get_pin(pin_id) for pin_id in ids]


def get_pin(pin_id):
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM scheduled_pins WHERE id = ?", (pin_id,)).fetchone()
    return dict(row) if row else None


def list_pins(status=None, batch_id=None, limit=50):
    query = "SELECT * FROM scheduled_pins"
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if batch_id:
        clauses.append("batch_id = ?")
        params.append(batch_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY publish_at, id LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def occupied_publish_times(since_iso):
    """publish_at values already taken by pins that will post or have posted;
    failed and cancelled pins free their slot."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT publish_at FROM scheduled_pins WHERE publish_at >= ? AND status IN (?, ?, ?)",
            (since_iso, STATUS_QUEUED, STATUS_POSTING, STATUS_POSTED),
        ).fetchall()
    return {datetime.strptime(row["publish_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for row in rows}


def due_pins(now=None, limit=20):
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_pins WHERE status = ? AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at, id LIMIT ?",
            (STATUS_QUEUED, now or now_iso(), limit),
        ).fetchall()
    return [dict(row) for row in rows]


def update_pin(row_id, /, **fields):
    # positional-only so a pin_id column value can be passed as a field
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with closing(_connect()) as conn, conn:
        conn.execute(f"UPDATE scheduled_pins SET {assignments} WHERE id = ?", (*fields.values(), row_id))


def cancel_pin(pin_id):
    """Cancels a pin that hasn't been posted yet. Returns False if it can't be cancelled."""
    with closing(_connect()) as conn, conn:
        cursor = conn.execute(
            "UPDATE scheduled_pins SET status = ? WHERE id = ? AND status = ?",
            (STATUS_CANCELLED, pin_id, STATUS_QUEUED),
        )
    return cursor.rowcount == 1


def requeue_interrupted():
    """A restart mid-post leaves rows in 'posting'; put them back in the queue."""
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE scheduled_pins SET status = ? WHERE status = ?", (STATUS_QUEUED, STATUS_POSTING))
