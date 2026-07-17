"""Schemas for the natural-language assistant ("Ask Scopio").

The user types a free-text instruction (e.g. "list businesses without a website and
make me an Excel file with phone, email and a Google Maps link"). The LLM parses it
into an `AssistantIntent`; the same intent object is echoed back by the client when
it asks for the file, so the export never needs a second LLM call.
"""
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Columns the assistant can put in results / exported files (order = display order).
ALLOWED_COLUMNS = [
    "name", "category", "phone", "email", "website",
    "address", "description", "status", "maps_link",
]
DEFAULT_COLUMNS = list(ALLOWED_COLUMNS)

_ALLOWED_STATUSES = {
    "discovered", "contacted", "interested", "callback_scheduled",
    "meeting_booked", "not_interested", "do_not_contact",
}


def _singular(word: str) -> str:
    """'cafes' → 'cafe', 'bakeries' → 'bakery' — DB categories are singular, users
    usually type plurals; matching is substring-ilike so the singular form wins."""
    w = word.strip().lower()
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


class AssistantFilters(BaseModel):
    """Which businesses the user asked about. None = don't filter on that axis."""
    has_website: bool | None = None
    has_phone: bool | None = None
    has_email: bool | None = None
    has_any_contact: bool | None = None      # phone OR email present
    categories: list[str] = Field(default_factory=list)   # matched ilike, e.g. ["cafe"]
    statuses: list[str] = Field(default_factory=list)
    name_contains: str | None = Field(default=None, max_length=120)

    @field_validator("statuses")
    @classmethod
    def _known_statuses(cls, v: list[str]) -> list[str]:
        return [s for s in (x.strip().lower() for x in v) if s in _ALLOWED_STATUSES]

    @field_validator("categories")
    @classmethod
    def _clean_categories(cls, v: list[str]) -> list[str]:
        return [_singular(c) for c in v if c and c.strip()][:10]


class AssistantIntent(BaseModel):
    """Structured form of the user's command — filters + what they want back."""
    # "query" = filter/list/export their leads; "chat" = a conversational answer
    # (advice, explanations, anything that isn't a leads lookup).
    mode: Literal["query", "chat"] = "query"
    reply: str = Field(default="", max_length=4000)        # the assistant's message
    summary: str = Field(default="", max_length=500)       # plain-English restatement
    scope: Literal["current_search", "all_leads"] = "all_leads"
    filters: AssistantFilters = Field(default_factory=AssistantFilters)
    wants_export: bool = False
    file_format: Literal["xlsx", "csv"] = "xlsx"
    group_by_category: bool = True
    columns: list[str] = Field(default_factory=lambda: list(DEFAULT_COLUMNS))
    # Brain decisions for chat mode: whether the DB alone can't satisfy the question
    # and the web tool (Tavily) should be called to enrich the answer.
    web_search: bool = False
    web_query: str | None = Field(default=None, max_length=300)

    @field_validator("columns")
    @classmethod
    def _known_columns(cls, v: list[str]) -> list[str]:
        cols = [c for c in (x.strip().lower() for x in v) if c in ALLOWED_COLUMNS]
        # keep canonical order, always include name; empty → default set
        cols = [c for c in ALLOWED_COLUMNS if c in cols]
        if not cols:
            return list(DEFAULT_COLUMNS)
        if "name" not in cols:
            cols.insert(0, "name")
        return cols


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class AssistantCommandIn(BaseModel):
    command: str = Field(min_length=1, max_length=4000)
    job_id: uuid.UUID | None = None          # the search open in the UI (if any)
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)  # chat memory


class AssistantItem(BaseModel):
    id: uuid.UUID
    name: str
    category: str | None
    phone: str | None
    email: str | None
    website: str | None
    address: str | None
    status: str
    description: str | None
    maps_link: str


class AssistantCommandOut(BaseModel):
    reply: str                                # the assistant's chat message
    mode: Literal["query", "chat"]
    summary: str
    intent: AssistantIntent                   # echo back for the export call
    items: list[AssistantItem]
    total: int
    grouped: dict[str, int]                   # category → count (for the UI chips)
    parser: Literal["llm", "heuristic"]       # how the command was understood
    used_web: bool = False                    # true if the web tool was called
    sources: list[dict] = Field(default_factory=list)  # [{title,url}] web citations


class AssistantExportIn(BaseModel):
    intent: AssistantIntent
    job_id: uuid.UUID | None = None


class AssistantCategoryIn(BaseModel):
    """Drill into one category from the grouped results — list its businesses."""
    intent: AssistantIntent
    job_id: uuid.UUID | None = None
    category: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class AssistantCategoryOut(BaseModel):
    category: str
    items: list[AssistantItem]
    total: int
    offset: int
