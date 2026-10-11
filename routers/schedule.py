"""Scheduled (drip) pin endpoints: schedulePins, getScheduledPins, cancelScheduledPin.

schedulePins saves each variant's image to disk and queues it with its own
publish time; pinterest/pin_worker.py posts them when they come due.
"""
import base64
import binascii
import io
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from PIL import Image
from pydantic import BaseModel, Field

from api_auth import require_api_key
from pinterest import pin_queue
from pinterest.pin_slots import next_free_slot
from pinterest.pinterest_pins import BASE64_IMAGE_TYPES, create_board, ensure_section, list_boards
from routers.pins import OpenAIFileRef, _image_source_url, _request_image_ref

router = APIRouter(dependencies=[Depends(require_api_key)])

MAX_IMAGE_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
# Pinterest's limits; checked here so the GPT can fix copy now instead of the pin failing days later
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
MAX_ALT_TEXT_CHARS = 500


# ---------- models ----------

class PinVariant(BaseModel):
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_CHARS, description="Pin title, at most 100 characters.")
    description: str = Field(
        ..., min_length=1, max_length=MAX_DESCRIPTION_CHARS, description="Pin description, at most 500 characters."
    )
    alt_text: str | None = Field(None, max_length=MAX_ALT_TEXT_CHARS, description="Optional alt text, at most 500 characters.")
    link: str | None = Field(None, description="Optional destination URL override; defaults to the request's link.")
    board_name: str | None = Field(
        None,
        description="Optional board for this pin, overriding the request's board. Resolved or created if missing.",
    )
    board_id: str | None = Field(None, description="Optional board ID for this pin, overriding the request's board.")
    section_name: str | None = Field(
        None,
        description="Optional section for this pin. Applies to this pin's own board if it sets one, otherwise to the request's board.",
    )
    image_url: str | None = Field(None, description="Legacy image URL fallback. Prefer openaiFileIdRefs.")
    image_base64: str | None = Field(None, description="Legacy base64 fallback. Prefer openaiFileIdRefs.")
    image_ref: OpenAIFileRef | None = Field(None, description="Single image reference fallback.")
    openaiFileIdRefs: list[OpenAIFileRef] | None = Field(
        None,
        description="OpenAI file references; the first reference is used as this variant's image.",
    )


class SchedulePinsRequest(BaseModel):
    board_name: str | None = Field(
        None,
        description="Default board for variants that don't set their own. Resolved or created if missing.",
    )
    board_id: str | None = Field(None, description="Default board ID for variants that don't set their own.")
    section_name: str | None = Field(
        None,
        description="Default section under the default board, for variants that don't set their own board or section.",
    )
    link: str = Field(..., description="Destination URL for every variant, usually the Etsy listing URL.")
    openaiFileIdRefs: list[OpenAIFileRef] | None = Field(
        None,
        description="One image per variant, in the same order as variants (image 1 for variant 1, and so on). "
                    "Used for any variant that doesn't carry its own image.",
    )
    variants: list[PinVariant] = Field(
        ...,
        min_length=1,
        max_length=4,
        description="One to four pin variants. The first posts at start_at, each later one spacing_days after the previous.",
    )
    spacing_days: float = Field(3, ge=0, le=60, description="Minimum days between consecutive variants. Defaults to 3.")
    start_at: datetime | None = Field(
        None,
        description="Earliest time the first variant may post (ISO 8601). Defaults to now.",
    )
    use_slots: bool = Field(
        True,
        description="When true (default), each variant goes into the next free daily posting slot so pins spread "
                    "evenly across days. When false, variants post at exactly start_at plus spacing_days.",
    )


class ScheduledPin(BaseModel):
    id: int = Field(..., description="Queue ID; use it with cancelScheduledPin.")
    batch_id: str = Field(..., description="Shared by all variants scheduled in one call.")
    title: str
    link: str
    board_id: str
    section_id: str | None = None
    publish_at: str = Field(..., description="Scheduled publish time (UTC).")
    status: str = Field(..., description="queued, posting, posted, failed, or cancelled.")
    attempts: int
    pin_id: str | None = Field(None, description="Pinterest pin ID once posted.")
    pin_url: str | None = Field(None, description="Public pin URL once posted.")
    last_error: str | None = Field(None, description="Most recent error, if any.")
    posted_at: str | None = None


class ScheduledPinsResponse(BaseModel):
    pins: list[ScheduledPin]


# ---------- helpers ----------

def _to_response(row):
    fields = {name: row[name] for name in ScheduledPin.model_fields if name in row}
    fields["pin_url"] = f"https://www.pinterest.com/pin/{row['pin_id']}/" if row["pin_id"] else None
    return ScheduledPin(**fields)


def _sniff_content_type(data):
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    return None


def _download(url):
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


class _BoardResolver:
    """Resolves each variant's board and section against the account's real
    boards, fetched once per request. Unknown board IDs are rejected now
    rather than failing with "Board not found" when the pin posts days later.
    (GPTs often garble long numeric IDs, and ensure_board can fall back to
    hard-coded IDs, so nothing is trusted without checking.)"""

    def __init__(self):
        self._account_boards = None  # lowercased name -> id
        self._sections = {}

    def _boards(self):
        if self._account_boards is None:
            self._account_boards = {board.get("name", "").strip().lower(): board["id"] for board in list_boards()}
        return self._account_boards

    def _board_id(self, board_id, board_name):
        boards = self._boards()
        if board_id:
            if board_id not in boards.values():
                raise ValueError(
                    f"Board ID {board_id} isn't one of this account's boards. "
                    "Use board_name with the exact name from getBoards instead."
                )
            return board_id
        key = board_name.strip().lower()
        if key not in boards:
            boards[key] = create_board(board_name.strip())["id"]
        return boards[key]

    def _section_id(self, board_id, section_name):
        if not section_name:
            return None
        key = (board_id, section_name.strip().lower())
        if key not in self._sections:
            section, _ = ensure_section(board_id, section_name)
            self._sections[key] = section["id"]
        return self._sections[key]

    def destination(self, variant, req):
        """(board_id, section_id) for a variant. A variant's own board replaces the
        request's board and section entirely; the request's section belongs to its board."""
        if variant.board_id or variant.board_name:
            board_id = self._board_id(variant.board_id, variant.board_name)
            return board_id, self._section_id(board_id, variant.section_name)
        board_id = self._board_id(req.board_id, req.board_name)
        return board_id, self._section_id(board_id, variant.section_name or req.section_name)


def _publish_times(start, count, spacing_days, use_slots):
    spacing = timedelta(days=spacing_days)
    if not use_slots:
        return [start + spacing * index for index in range(count)]

    occupied = pin_queue.occupied_publish_times(pin_queue.utc_iso(start))
    times = []
    earliest = start
    for _ in range(count):
        slot = next_free_slot(earliest, occupied)
        occupied.add(slot)
        times.append(slot)
        # measure spacing from the slot actually used so a late slot never shrinks the gap
        earliest = slot + spacing
    return times


def _variant_image(variant, fallback_ref=None):
    """Returns (bytes, content_type) for a variant, fetching remote images now
    because their links may expire before the pin is posted. fallback_ref is the
    matching top-level file ref, used when the variant has no image of its own."""
    if fallback_ref and not (variant.image_base64 or variant.image_url or variant.image_ref or variant.openaiFileIdRefs):
        variant = variant.model_copy(update={"image_ref": fallback_ref})
    if variant.image_base64:
        try:
            data = base64.b64decode(variant.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Invalid image_base64: {exc}") from exc
    else:
        url = _image_source_url(_request_image_ref(variant.image_ref, variant.openaiFileIdRefs), variant.image_url)
        if not url:
            raise ValueError("Provide openaiFileIdRefs, image_ref, image_url, or image_base64.")
        try:
            data = _download(url)
        except requests.RequestException as exc:
            raise ValueError(f"Could not download the image: {exc}") from exc

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image is larger than 20 MB.")
    content_type = _sniff_content_type(data)
    if not content_type:
        raise ValueError("The image must be a PNG, JPEG, WebP, or GIF file.")
    if content_type not in BASE64_IMAGE_TYPES:
        data, content_type = _to_png(data), "image/png"
    return data, content_type


def _to_png(data):
    """Pinterest only takes PNG or JPEG image data, so convert WebP/GIF up front."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            output = io.BytesIO()
            image.convert("RGBA").save(output, format="PNG")
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read the image: {exc}") from exc
    return output.getvalue()


# ---------- endpoints ----------

@router.post(
    "/pins/schedule",
    operation_id="schedulePins",
    summary="Queue pin variants to post on a drip schedule",
    description="Saves one to four pin variants and posts them spaced out over time (default: first now, "
                "then every 3 days). Images are stored immediately, so temporary file links are fine. "
                "Returns the queued pins; check progress with getScheduledPins.",
    response_model=ScheduledPinsResponse,
)
def schedule_pins_endpoint(req: SchedulePinsRequest):
    for number, variant in enumerate(req.variants, start=1):
        if not (variant.board_id or variant.board_name or req.board_id or req.board_name):
            raise HTTPException(
                status_code=400,
                detail=f"Variant {number}: set board_name or board_id on the variant or the request.",
            )

    # fetch and validate every image before touching Pinterest or the queue,
    # so one bad variant rejects the whole request instead of half-queuing it
    # ChatGPT may only fill openaiFileIdRefs at the top level, so map those to variants by position
    top_level_refs = req.openaiFileIdRefs or []
    images = []
    for number, variant in enumerate(req.variants, start=1):
        fallback_ref = top_level_refs[number - 1] if number <= len(top_level_refs) else None
        try:
            images.append(_variant_image(variant, fallback_ref))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Variant {number}: {exc}") from exc

    resolver = _BoardResolver()
    destinations = []
    for number, variant in enumerate(req.variants, start=1):
        try:
            destinations.append(resolver.destination(variant, req))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Variant {number}: {exc}") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Variant {number}: could not resolve the board or section: {exc}"
            ) from exc

    start = req.start_at or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    try:
        publish_times = _publish_times(start, len(req.variants), req.spacing_days, req.use_slots)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    rows = []
    for variant, (data, content_type), publish_at, (board_id, section_id) in zip(
        req.variants, images, publish_times, destinations
    ):
        rows.append(
            {
                "board_id": board_id,
                "section_id": section_id,
                "title": variant.title,
                "description": variant.description,
                "link": variant.link or req.link,
                "alt_text": variant.alt_text,
                "image_path": pin_queue.save_image(data, content_type),
                "content_type": content_type,
                "publish_at": pin_queue.utc_iso(publish_at),
            }
        )
    return ScheduledPinsResponse(pins=[_to_response(row) for row in pin_queue.add_pins(rows)])


@router.get(
    "/pins/scheduled",
    operation_id="getScheduledPins",
    summary="List scheduled pins and their status",
    description="Returns queued, posted, failed, and cancelled pins, soonest first. Filter by status or batch_id.",
    response_model=ScheduledPinsResponse,
)
def get_scheduled_pins_endpoint(
    status: str | None = Query(None, description="queued, posting, posted, failed, or cancelled."),
    batch_id: str | None = Query(None, description="Only pins from one schedulePins call."),
    limit: int = Query(50, ge=1, le=200),
):
    rows = pin_queue.list_pins(status=status, batch_id=batch_id, limit=limit)
    return ScheduledPinsResponse(pins=[_to_response(row) for row in rows])


@router.delete(
    "/pins/scheduled/{pin_id}",
    operation_id="cancelScheduledPin",
    summary="Cancel a scheduled pin that hasn't posted yet",
    response_model=ScheduledPin,
)
def cancel_scheduled_pin_endpoint(pin_id: int):
    row = pin_queue.get_pin(pin_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No scheduled pin with that ID.")
    if not pin_queue.cancel_pin(pin_id):
        raise HTTPException(status_code=409, detail=f"Only queued pins can be cancelled; this one is {row['status']}.")
    return _to_response(pin_queue.get_pin(pin_id))
