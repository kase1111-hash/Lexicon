"""LSR (Lexical State Record) API routes."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/{lsr_id}")
async def get_lsr(lsr_id: UUID):
    """Get a full LSR record by ID."""
    # TODO: Implement LSR retrieval
    raise HTTPException(status_code=404, detail="LSR not found")


@router.get("/search")
async def search_lsr(
    form: Optional[str] = Query(None, description="Form to search (exact or fuzzy)"),
    language: Optional[str] = Query(None, description="ISO language code"),
    date_start: Optional[int] = Query(None, description="Start of date range"),
    date_end: Optional[int] = Query(None, description="End of date range"),
    semantic_field: Optional[str] = Query(None, description="Semantic field filter"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Search for LSRs matching criteria."""
    # TODO: Implement search
    return {"results": [], "total": 0, "limit": limit, "offset": offset}


@router.get("/{lsr_id}/etymology")
async def get_etymology(lsr_id: UUID):
    """Get the full ancestor chain to proto-form."""
    # TODO: Implement etymology chain retrieval
    return {"lsr_id": str(lsr_id), "chain": [], "proto_form": None}


@router.get("/{lsr_id}/descendants")
async def get_descendants(lsr_id: UUID, depth: int = Query(3, ge=1, le=10)):
    """Get descendant tree."""
    # TODO: Implement descendant tree retrieval
    return {"lsr_id": str(lsr_id), "descendants": [], "depth": depth}


@router.get("/{lsr_id}/cognates")
async def get_cognates(lsr_id: UUID):
    """Get all cognate LSRs across languages."""
    # TODO: Implement cognate retrieval
    return {"lsr_id": str(lsr_id), "cognates": []}
