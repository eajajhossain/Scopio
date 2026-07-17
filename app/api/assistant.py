"""Ask Scopio — an agentic mini-chatbot over the user's discovered leads.

POST /assistant/command   → the LLM plans; we either LIST matching businesses
                            (query mode) or ANSWER the question from the database
                            + the web tool (chat mode). LLM = brain, DB = source.
POST /assistant/category  → drill into one category: all its businesses (paginated).
POST /assistant/export    → the whole result set as .xlsx / .csv (clickable links).
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_tenant_id
from app.models.tenant import Tenant
from app.schemas.assistant import (
    AssistantCategoryIn,
    AssistantCategoryOut,
    AssistantCommandIn,
    AssistantCommandOut,
    AssistantExportIn,
    AssistantItem,
)
from app.services import assistant

router = APIRouter(prefix="/assistant", tags=["assistant"])


def _item(biz) -> AssistantItem:
    return AssistantItem(
        id=biz.id, name=biz.name, category=assistant.display_category(biz),
        phone=biz.phone, email=biz.email, website=biz.website, address=biz.address,
        status=biz.status, description=assistant.description_of(biz),
        maps_link=assistant.maps_link(biz.name, biz.address, biz.lat, biz.lng),
    )


@router.post("/command", response_model=AssistantCommandOut)
async def run_command(
    body: AssistantCommandIn,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    """Plan the user's message, then list leads or answer from the data + web."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    services = tenant.services if tenant else None
    company = tenant.company_name if tenant else None
    job_id = str(body.job_id) if body.job_id else None
    history = [t.model_dump() for t in body.history]

    # A live snapshot of the on-screen leads lets the planner reference the real data.
    snapshot = await assistant.data_snapshot(db, job_id)
    intent, parser = await assistant.parse_command(
        body.command, services=services, company=company,
        history=history, data_context=snapshot,
    )

    if intent.mode == "chat":
        # Agentic answer: retrieve from the DB (+ web tool if the brain asked), then
        # the LLM writes the answer from that data — it never answers from thin air.
        reply, used_web, sources = await assistant.answer_with_data(
            db, intent, body.command,
            services=services, company=company, job_id=job_id, history=history,
        )
        return AssistantCommandOut(
            reply=reply, mode="chat", summary=intent.summary, intent=intent,
            items=[], total=0, grouped={}, parser=parser,
            used_web=used_web, sources=sources,
        )

    rows, total = await assistant.run_command_query(db, intent, job_id)
    # Businesses with no type yet get one from the AI, so grouping is meaningful.
    await assistant.categorize_missing(db, rows)

    items = [_item(b) for b in rows]
    # Type counts over the WHOLE match set (the table shows a page; drill-in shows all).
    grouped = await assistant.grouped_counts(db, intent, job_id)

    return AssistantCommandOut(
        reply=intent.reply or intent.summary or "Here's what I found:",
        mode="query", summary=intent.summary or "Here's what I found.",
        intent=intent, items=items, total=total, grouped=grouped, parser=parser,
    )


@router.post("/category", response_model=AssistantCategoryOut)
async def list_category(
    body: AssistantCategoryIn,
    db: AsyncSession = Depends(get_db),
):
    """All businesses of one category within the current result set (click-to-expand)."""
    job_id = str(body.job_id) if body.job_id else None
    rows, total = await assistant.list_by_category(
        db, body.intent, job_id, body.category, body.limit, body.offset,
    )
    return AssistantCategoryOut(
        category=body.category, items=[_item(b) for b in rows],
        total=total, offset=body.offset,
    )


@router.post("/export")
async def export_file(
    body: AssistantExportIn,
    db: AsyncSession = Depends(get_db),
):
    """Build the spreadsheet for a previously parsed intent — the WHOLE match set."""
    job_id = str(body.job_id) if body.job_id else None
    rows, _total = await assistant.run_command_query(
        db, body.intent, job_id, limit=assistant.EXPORT_MAX,
    )
    data = [assistant.row_for(b, body.intent.columns) for b in rows]

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    if body.intent.file_format == "xlsx":
        content = assistant.build_xlsx(data, body.intent.columns)
        if content is not None:
            return Response(
                content,
                media_type=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                headers={"Content-Disposition":
                         f'attachment; filename="scopio-leads-{stamp}.xlsx"'},
            )
        # openpyxl missing → CSV still opens in Excel
    content = assistant.build_csv(data, body.intent.columns)
    return Response(
        content, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="scopio-leads-{stamp}.csv"'},
    )
