"""
Integration aliases for GSC. The frontend's diagnostic probe checks
/api/integrations/gsc/queries among several legacy paths. This route
forwards to the canonical handler in api.routes.seo so all probes land
on the same payload regardless of historical naming.
"""

from fastapi import APIRouter, HTTPException, Request

from api.routes.seo import _gsc_queries_payload

router = APIRouter()


@router.get("/queries")
@router.get("/top-queries")
async def get_gsc_queries(request: Request, limit: int = 1000):
    try:
        return await _gsc_queries_payload(request, limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
