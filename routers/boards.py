"""Board/category endpoints: getBoards, ensureBoard, ensureSection."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api_auth import require_api_key
from pinterest.pinterest_boards import BOARD_MAP
from pinterest.pinterest_pins import ensure_board, ensure_section, list_boards

router = APIRouter(dependencies=[Depends(require_api_key)])


# ---------- models ----------

class BoardsResponse(BaseModel):
    boards: list[dict[str, str]] = Field(..., description="Pinterest board IDs and names.")


class CategoriesResponse(BaseModel):
    categories: list[str] = Field(..., description="Valid category values for createPin.")


class EnsureBoardRequest(BaseModel):
    name: str = Field(..., description="Board name to ensure exists on the active Pinterest environment.")
    description: str | None = Field(None, description="Optional board description.")
    section_name: str | None = Field(None, description="Optional section name to create under the board.")


class EnsureSectionRequest(BaseModel):
    board_id: str | None = Field(None, description="Board ID to ensure the section exists on.")
    board_name: str | None = Field(None, description="Board name to resolve if board_id is not provided.")
    section_name: str = Field(..., description="Section name to ensure exists.")


class EnsureBoardResponse(BaseModel):
    board_id: str = Field(..., description="Resolved Pinterest board ID.")
    board_name: str = Field(..., description="Pinterest board name.")
    section_id: str | None = Field(None, description="Resolved section ID when a section was requested.")
    section_name: str | None = Field(None, description="Requested section name.")
    created: bool = Field(..., description="Whether the board was created during this call.")
    section_created: bool | None = Field(None, description="Whether the section was created during this call.")


# ---------- endpoints ----------

@router.get(
    "/boards",
    operation_id="getBoards",
    summary="List Pinterest boards",
    description="Returns board IDs and names from the authenticated Pinterest account.",
    response_model=BoardsResponse,
)
def get_boards():
    try:
        boards = list_boards()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not retrieve Pinterest boards.") from exc
    return BoardsResponse(boards=[{"id": board["id"], "name": board["name"]} for board in boards])


@router.get(
    "/categories",
    operation_id="getCategories",
    summary="List valid pin categories",
    description="Returns the category values accepted by createPin.",
    response_model=CategoriesResponse,
)
def get_categories():
    return CategoriesResponse(categories=list(BOARD_MAP.keys()))


@router.post(
    "/boards/ensure",
    operation_id="ensureBoard",
    summary="Ensure a board exists and optionally create a section",
    description="Creates a Pinterest board if it does not already exist and optionally creates a named section on it.",
    response_model=EnsureBoardResponse,
)
def ensure_board_endpoint(req: EnsureBoardRequest):
    board, created = ensure_board(req.name, description=req.description)
    section_id = None
    section_name = req.section_name
    section_created = None
    if req.section_name:
        section, section_created = ensure_section(board["id"], req.section_name)
        section_id = section["id"]
    return EnsureBoardResponse(
        board_id=board["id"],
        board_name=board["name"],
        section_id=section_id,
        section_name=section_name,
        created=created,
        section_created=section_created,
    )


@router.post(
    "/board-sections/ensure",
    operation_id="ensureSection",
    summary="Ensure a section exists on a board",
    description="Creates a section on a board by board_id or board_name if it does not already exist.",
    response_model=EnsureBoardResponse,
)
def ensure_section_endpoint(req: EnsureSectionRequest):
    board_id = req.board_id
    board_name = ""
    if not board_id:
        if not req.board_name:
            raise HTTPException(status_code=400, detail="Either board_id or board_name must be provided.")
        board, _ = ensure_board(req.board_name)
        board_id = board["id"]
        board_name = board["name"]
    section, section_created = ensure_section(board_id, req.section_name)
    return EnsureBoardResponse(
        board_id=board_id,
        board_name=board_name,
        section_id=section["id"],
        section_name=req.section_name,
        created=False,
        section_created=section_created,
    )
