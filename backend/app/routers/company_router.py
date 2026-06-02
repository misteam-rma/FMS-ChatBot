"""
Botivate HR Support - Simplified Company Router
Only basic company info endpoints remain - no registration, policies, or manager features.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.schemas import CompanyResponse, CompanySupportInfo
from app.services import company_service

router = APIRouter(prefix="/api/companies", tags=["Companies"])


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(company_id: str, db: AsyncSession = Depends(get_db)):
    """Get company details by ID."""
    company = await company_service.get_company(db, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/{company_id}/support", response_model=CompanySupportInfo)
async def get_support_info(company_id: str, db: AsyncSession = Depends(get_db)):
    """Get company support contact info."""
    company = await company_service.get_company(db, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return CompanySupportInfo(
        company_name=company.name,
        support_email=company.support_email,
        support_phone=company.support_phone,
        support_whatsapp=company.support_whatsapp,
        support_message=company.support_message,
    )
