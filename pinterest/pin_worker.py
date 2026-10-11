"""
Background worker that posts scheduled pins when they come due.

Runs as a daemon thread inside the web service (started from api.py's
lifespan), checking the queue every PIN_WORKER_INTERVAL seconds.

Retry rule: Pinterest sometimes rejects the first attempt at an Etsy link
with HTTP 400 "Sorry! This site doesn't allow you to save Pins." and then
accepts the same pin a minute later. Only that error is retried; any other
failure (bad board, bad image) won't fix itself, so the row fails at once.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from pinterest import pin_queue
from pinterest.pinterest_pins import create_pin

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 60
SECONDS_BETWEEN_PINS = 2  # polite pacing when several pins come due together
DEFAULT_INTERVAL_SECONDS = 60  # a due-row check is one cheap SQLite query

_run_lock = threading.Lock()


def is_retryable(error):
    # Pinterest has used both straight and curly apostrophes in this message
    return "allow you to save pins" in str(error).lower()


def _post(row):
    with open(row["image_path"], "rb") as f:
        image_bytes = f.read()
    return create_pin(
        board_id=row["board_id"],
        title=row["title"],
        description=row["description"],
        link=row["link"],
        alt_text=row["alt_text"],
        board_section_id=row["section_id"],
        image_bytes=image_bytes,
        content_type=row["content_type"],
    )


def process_due_pins(now=None, sleep=time.sleep):
    """Posts every queued pin whose next attempt is due. Returns a count per outcome."""
    counts = {"posted": 0, "retrying": 0, "failed": 0}
    # skip rather than overlap if a previous run is still going
    if not _run_lock.acquire(blocking=False):
        return counts
    try:
        due = pin_queue.due_pins(now=now)
        for index, row in enumerate(due):
            if index:
                sleep(SECONDS_BETWEEN_PINS)
            attempts = row["attempts"] + 1
            pin_queue.update_pin(row["id"], status=pin_queue.STATUS_POSTING, attempts=attempts)
            try:
                result = _post(row)
            except Exception as exc:
                if is_retryable(exc) and attempts < MAX_ATTEMPTS:
                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_SECONDS)
                    pin_queue.update_pin(
                        row["id"],
                        status=pin_queue.STATUS_QUEUED,
                        next_attempt_at=pin_queue.utc_iso(retry_at),
                        last_error=str(exc),
                    )
                    counts["retrying"] += 1
                else:
                    pin_queue.update_pin(row["id"], status=pin_queue.STATUS_FAILED, last_error=str(exc))
                    counts["failed"] += 1
                continue
            pin_queue.update_pin(
                row["id"],
                status=pin_queue.STATUS_POSTED,
                pin_id=result.get("id"),
                posted_at=pin_queue.now_iso(),
                last_error=None,
            )
            counts["posted"] += 1
    finally:
        _run_lock.release()
    return counts


def start_background_worker():
    """Starts the polling thread unless PIN_WORKER_ENABLED=0. Returns a stop function."""
    if os.environ.get("PIN_WORKER_ENABLED", "1").strip() == "0":
        return lambda: None

    interval = int(os.environ.get("PIN_WORKER_INTERVAL", DEFAULT_INTERVAL_SECONDS))
    stop_event = threading.Event()
    pin_queue.requeue_interrupted()

    def loop():
        while not stop_event.is_set():
            try:
                counts = process_due_pins()
                if any(counts.values()):
                    print(f"pin worker: {counts}")
            except Exception as exc:  # keep the thread alive through one bad run
                print(f"pin worker error: {exc}")
            stop_event.wait(interval)

    threading.Thread(target=loop, name="pin-worker", daemon=True).start()
    return stop_event.set
