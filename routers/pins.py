"""Pin-publishing endpoints: createPin, createPinByBoard, and their batch forms."""
import base64
import binascii
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api_auth import require_api_key
from pinterest.pinterest_batch import create_pins_batch
from pinterest.pinterest_pipeline import create_pin_for_listing
from pinterest.pinterest_pins import create_pin, ensure_board, ensure_section

router = APIRouter(dependencies=[Depends(require_api_key)])


# ---------- models ----------

class OpenAIFileRef(BaseModel):
    name: str | None = Field(None, description="Optional file name from the generator.")
    id: str | None = Field(None, description="Optional file ID from the generator.")
    mime_type: str | None = Field(None, description="Optional MIME type reported by the generator.")
    download_link: str = Field(..., description="Temporary file URL Pinterest fetches when creating the pin.")


class PinRequest(BaseModel):
    product_title: str = Field(..., description="The actual product name.")
    category: str = Field(..., description="A category value returned by getCategories.")
    link: str = Field(..., description="Destination URL the pin should point to.")
    image_url: str | None = Field(None, description="Legacy image URL fallback. Prefer openaiFileIdRefs.")
    image_base64: str | None = Field(None, description="Legacy base64 fallback. Prefer openaiFileIdRefs.")
    image_ref: OpenAIFileRef | None = Field(None, description="Single image reference fallback.")
    openaiFileIdRefs: list[OpenAIFileRef] | None = Field(
        None,
        description="OpenAI file references; the first reference is used as the pin image.",
    )
    title: str = Field(..., description="Pin title from the publishing package.")
    description: str = Field(..., description="Pin description from the publishing package.")


class BatchRequest(BaseModel):
    pins: list[PinRequest] = Field(..., min_length=1, max_length=5, description="Up to five pins to create, in order.")


class CreatePinByBoardRequest(BaseModel):
    board_name: str | None = Field(None, description="Board name to resolve or create if missing.")
    board_id: str | None = Field(None, description="Board ID to publish the pin to.")
    section_name: str | None = Field(None, description="Optional section name to resolve or create under the board.")
    title: str = Field(..., description="Pin title.")
    description: str = Field(..., description="Pin description.")
    link: str = Field(..., description="Destination URL the pin should point to.")
    image_url: str | None = Field(None, description="Legacy image URL fallback. Prefer image_ref.download_link when available.")
    image_base64: str | None = Field(None, description="Legacy base64 fallback. Prefer image_ref.download_link when available.")
    image_ref: OpenAIFileRef | None = Field(None, description="Preferred image source; the server downloads and uploads this to Pinterest.")
    openaiFileIdRefs: list[OpenAIFileRef] | None = Field(
        None,
        description="OpenAI file references; the first reference is used as the pin image.",
    )
    alt_text: str | None = Field(None, description="Optional alt text for the pin.")


class CreatePinsBatchByBoardRequest(BaseModel):
    pins: list[CreatePinByBoardRequest] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="Up to five pins to create in order. Each entry resolves its own board/section.",
    )


class PinResponse(BaseModel):
    pin_id: str = Field(..., description="Pinterest's ID for the created pin.")
    pin_url: str = Field(..., description="Public URL of the created pin.")
    board_id: str | None = Field(None, description="Resolved Pinterest board ID when using a board-aware endpoint.")
    section_id: str | None = Field(None, description="Resolved Pinterest section ID when a section was used.")


class BatchResultItem(BaseModel):
    status: str = Field(..., description='"ok" or "failed".')
    pin_id: str | None = Field(None, description="Set when status is ok.")
    pin_url: str | None = Field(None, description="Set when status is ok.")
    board_id: str | None = Field(None, description="Resolved Pinterest board ID when the pin was created.")
    section_id: str | None = Field(None, description="Resolved Pinterest section ID when a section was used.")
    error: str | None = Field(None, description="Set when status is failed.")


class BatchResponse(BaseModel):
    results: list[BatchResultItem] = Field(..., description="One result per submitted pin, same order.")


# ---------- helpers ----------

def _pin_url(pin_id):
    return f"https://www.pinterest.com/pin/{pin_id}/"


def _image_source_url(image_ref: OpenAIFileRef | None, image_url: str | None) -> str | None:
    source_url = image_ref.download_link if image_ref is not None else image_url
    if source_url and urlparse(source_url).scheme.lower() not in {"http", "https"}:
        raise ValueError(
            "The image URL must be an http:// or https:// URL that Pinterest can reach. "
            "GPT sandbox paths such as sandbox:/mnt/data/... are not accessible to Pinterest."
        )
    return source_url


def _request_image_ref(image_ref: OpenAIFileRef | None, file_refs: list[OpenAIFileRef] | None) -> OpenAIFileRef | None:
    return image_ref or (file_refs[0] if file_refs else None)


# ---------- endpoints ----------

@router.post(
    "/create-pin-by-board",
    operation_id="createPinByBoard",
    summary="Publish one pin to a board or section by name",
    description="Resolves or creates the configured board and optional section, then creates a pin using an image URL or base64 payload.",
    response_model=PinResponse,
)
def create_pin_by_board_endpoint(req: CreatePinByBoardRequest):
    image_ref = req.image_ref or (req.openaiFileIdRefs[0] if req.openaiFileIdRefs else None)
    if not req.board_id and not req.board_name:
        raise HTTPException(status_code=400, detail="Either board_id or board_name is required.")
    if not req.image_url and not req.image_base64 and not image_ref:
        raise HTTPException(status_code=400, detail="Provide openaiFileIdRefs, image_ref, image_url, or image_base64.")

    board_id = req.board_id
    if not board_id:
        board, _ = ensure_board(req.board_name)
        board_id = board["id"]

    section_id = None
    if req.section_name:
        section, _ = ensure_section(board_id, req.section_name)
        section_id = section["id"]

    image_bytes = None
    pinterest_image_url = None
    if req.image_base64:
        try:
            image_bytes = base64.b64decode(req.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid image_base64: {exc}") from exc
    else:
        try:
            pinterest_image_url = _image_source_url(image_ref, req.image_url)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid image source: {exc}") from exc

    try:
        result = create_pin(
            board_id=board_id,
            title=req.title,
            description=req.description,
            link=req.link,
            image_url=pinterest_image_url,
            alt_text=req.alt_text,
            board_section_id=section_id,
            image_bytes=image_bytes,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pin_id = result.get("id")
    return PinResponse(
        pin_id=pin_id,
        pin_url=_pin_url(pin_id),
        board_id=board_id,
        section_id=section_id,
    )


@router.post(
    "/create-pin",
    operation_id="createPin",
    summary="Publish one pin to Pinterest",
    description="Creates a single pin on the board mapped to the given category. "
                "Returns the created pin's ID and public URL.",
    response_model=PinResponse,
)
def create_pin_endpoint(req: PinRequest):
    image_ref = _request_image_ref(req.image_ref, req.openaiFileIdRefs)
    if not req.image_url and not req.image_base64 and not image_ref:
        raise HTTPException(status_code=400, detail="Provide openaiFileIdRefs, image_ref, image_url, or image_base64.")
    if req.image_base64:
        raise HTTPException(status_code=400, detail="image_base64 is not supported by the non-board route; use openaiFileIdRefs or image_url.")
    try:
        result = create_pin_for_listing(
            product_title=req.product_title,
            theme="",  # unused when title/description are supplied
            category=req.category,
            link=req.link,
            image_url=_image_source_url(image_ref, req.image_url),
            title=req.title,
            description=req.description,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    pin_id = result.get("id")
    return PinResponse(pin_id=pin_id, pin_url=_pin_url(pin_id))


@router.post(
    "/create-pins-batch-by-board",
    operation_id="createPinsBatchByBoard",
    summary="Publish several pins to boards or sections by name",
    description="Resolves or creates each board and optional section before publishing the pins in one batch.",
    response_model=BatchResponse,
)
def create_pins_batch_by_board_endpoint(req: CreatePinsBatchByBoardRequest):
    results = []
    for item in req.pins:
        try:
            image_ref = item.image_ref or (item.openaiFileIdRefs[0] if item.openaiFileIdRefs else None)
            if not item.board_id and not item.board_name:
                raise ValueError("Either board_id or board_name is required.")
            if not item.image_url and not item.image_base64 and not image_ref:
                raise ValueError("Provide openaiFileIdRefs, image_ref, image_url, or image_base64.")

            board_id = item.board_id
            if not board_id:
                board, _ = ensure_board(item.board_name)
                board_id = board["id"]

            section_id = None
            if item.section_name:
                section, _ = ensure_section(board_id, item.section_name)
                section_id = section["id"]

            image_bytes = None
            pinterest_image_url = None
            if item.image_base64:
                image_bytes = base64.b64decode(item.image_base64)
            else:
                pinterest_image_url = _image_source_url(image_ref, item.image_url)

            result = create_pin(
                board_id=board_id,
                title=item.title,
                description=item.description,
                link=item.link,
                image_url=pinterest_image_url,
                alt_text=item.alt_text,
                board_section_id=section_id,
                image_bytes=image_bytes,
            )
            pin_id = result.get("id")
            results.append(
                BatchResultItem(
                    status="ok",
                    pin_id=pin_id,
                    pin_url=_pin_url(pin_id),
                    board_id=board_id,
                    section_id=section_id,
                )
            )
        except Exception as exc:
            results.append(BatchResultItem(status="failed", error=str(exc)))
    return BatchResponse(results=results)


@router.post(
    "/create-pins-batch",
    operation_id="createPinsBatch",
    summary="Publish several pins to Pinterest",
    description="Creates multiple pins in one call. Each pin succeeds or fails independently — "
                "one failure does not stop the rest.",
    response_model=BatchResponse,
)
def create_pins_batch_endpoint(req: BatchRequest):
    results: list[BatchResultItem | None] = [None] * len(req.pins)
    listings = []
    listing_indexes = []

    for index, pin in enumerate(req.pins):
        if pin.image_base64:
            results[index] = BatchResultItem(
                status="failed",
                error="image_base64 is not supported by the non-board batch route; use an image reference or image_url.",
            )
            continue

        try:
            image_url = _image_source_url(
                _request_image_ref(pin.image_ref, pin.openaiFileIdRefs),
                pin.image_url,
            )
        except ValueError as exc:
            results[index] = BatchResultItem(status="failed", error=str(exc))
            continue

        if not image_url:
            results[index] = BatchResultItem(
                status="failed",
                error="Provide openaiFileIdRefs, image_ref, or image_url.",
            )
            continue

        listings.append(
            {
                "product_title": pin.product_title,
                "theme": "",
                "category": pin.category,
                "link": pin.link,
                "image_url": image_url,
                "title": pin.title,
                "description": pin.description,
            }
        )
        listing_indexes.append(index)

    if listings:
        raw_results = create_pins_batch(listings)
        for index, result in zip(listing_indexes, raw_results):
            if result["status"] == "ok":
                results[index] = BatchResultItem(
                    status="ok",
                    pin_id=result["pin_id"],
                    pin_url=_pin_url(result["pin_id"]),
                )
            else:
                results[index] = BatchResultItem(status="failed", error=result["error"])

    return BatchResponse(results=[result for result in results if result is not None])
