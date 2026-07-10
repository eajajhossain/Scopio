import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.models.business import Business
from app.schemas.business import BusinessOut, BusinessUpdate

router = APIRouter(prefix="/businesses", tags=["businesses"])


async def _get_active(db: AsyncSession, business_id: uuid.UUID) -> Business:
    biz = (
        await db.execute(
            select(Business).where(
                Business.id == business_id, Business.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if biz is None:
        raise HTTPException(status_code=404, detail="business not found")
    return biz


@router.get("/{business_id}", response_model=BusinessOut)
async def get_business(business_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await _get_active(db, business_id)


@router.patch("/{business_id}", response_model=BusinessOut)
async def update_business(
    business_id: uuid.UUID,
    body: BusinessUpdate,
    db: AsyncSession = Depends(get_db),
):
    biz = await _get_active(db, business_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(biz, field, value)
    await db.commit()
    await db.refresh(biz)
    return biz


@router.delete("/{business_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_business(business_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    biz = await _get_active(db, business_id)
    biz.deleted_at = datetime.now(UTC)
    await db.commit()
