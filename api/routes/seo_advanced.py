"""
Advanced SEO API Routes
Schema markup, Google Indexing API, internal linking
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import logging

from agents.seo_schema import schema_generator
from agents.seo_indexing import indexing_api
from agents.seo_internal_linking import internal_linking_optimizer

router = APIRouter()
logger = logging.getLogger(__name__)


class SchemaGenerationRequest(BaseModel):
    type: str  # "article", "faq", "howto", "product", "breadcrumb"
    data: Dict[str, Any]


class IndexingRequest(BaseModel):
    url: str
    action: str = "update"  # "update" or "delete"


class InternalLinkingRequest(BaseModel):
    content: str
    current_url: str


@router.post("/schema/generate")
async def generate_schema_markup(request: SchemaGenerationRequest):
    """
    Generate JSON-LD schema markup
    
    Types:
    - article: Blog posts
    - faq: FAQ pages
    - howto: How-to guides
    - product: Product pages
    - breadcrumb: Navigation breadcrumbs
    """
    try:
        schema = None
        
        if request.type == "article":
            schema = schema_generator.generate_article_schema(**request.data)
        elif request.type == "faq":
            schema = schema_generator.generate_faq_schema(request.data.get("faqs", []))
        elif request.type == "howto":
            schema = schema_generator.generate_howto_schema(**request.data)
        elif request.type == "product":
            schema = schema_generator.generate_product_schema(**request.data)
        elif request.type == "breadcrumb":
            schema = schema_generator.generate_breadcrumb_schema(request.data.get("breadcrumbs", []))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown schema type: {request.type}")
        
        # Validate schema
        validation = schema_generator.validate_schema(schema)
        
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail=f"Invalid schema: {validation['errors']}")
        
        # Generate script tag
        script_tag = schema_generator.combine_schemas([schema])
        
        return {
            "success": True,
            "schema": schema,
            "script_tag": script_tag,
            "validation": validation
        }
        
    except Exception as e:
        logger.error(f"Failed to generate schema: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/indexing/notify")
async def notify_google_indexing(request: IndexingRequest):
    """
    Notify Google Indexing API about URL changes
    
    Actions:
    - update: URL has been updated or created
    - delete: URL has been removed
    """
    try:
        if request.action == "update":
            result = await indexing_api.notify_url_updated(request.url)
        elif request.action == "delete":
            result = await indexing_api.notify_url_deleted(request.url)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to notify Google Indexing API: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/indexing/batch")
async def batch_notify_indexing(urls: List[str], action: str = "update"):
    """Batch notify multiple URLs to Google Indexing API"""
    try:
        result = await indexing_api.batch_notify_urls(urls, action.upper())
        return result
        
    except Exception as e:
        logger.error(f"Failed to batch notify: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/internal-linking/analyze")
async def analyze_internal_linking(request: InternalLinkingRequest):
    """
    Analyze content and suggest internal links
    
    Returns suggested links with anchor text and target URLs
    """
    try:
        result = await internal_linking_optimizer.analyze_content_for_links(
            request.content,
            request.current_url
        )
        
        return {
            "success": True,
            **result
        }
        
    except Exception as e:
        logger.error(f"Failed to analyze internal linking: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/internal-linking/graph")
async def get_link_graph():
    """
    Get site-wide internal linking graph
    
    Shows:
    - Orphan pages (no incoming links)
    - Hub pages (many outgoing links)
    - Authority pages (many incoming links)
    """
    try:
        result = await internal_linking_optimizer.generate_link_graph()
        
        return {
            "success": True,
            **result
        }
        
    except Exception as e:
        logger.error(f"Failed to generate link graph: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/internal-linking/pillar/{pillar}")
async def get_pillar_link_suggestions(pillar: str):
    """
    Get internal linking suggestions for a content pillar
    
    Suggests which supporting articles should link to pillar page
    """
    try:
        result = await internal_linking_optimizer.suggest_pillar_page_links(pillar)
        
        return {
            "success": True,
            "pillar": pillar,
            "suggestions": result
        }
        
    except Exception as e:
        logger.error(f"Failed to get pillar link suggestions: {e}")
        raise HTTPException(status_code=500, detail=str(e))
