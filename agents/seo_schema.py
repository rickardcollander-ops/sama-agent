"""
Schema Markup Management for SEO Agent
Generates and validates JSON-LD structured data
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


class SchemaMarkupGenerator:
    """Generate JSON-LD schema markup for different content types"""
    
    def __init__(self):
        self.base_organization = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "Successifier",
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Web",
            "offers": {
                "@type": "Offer",
                "price": "0",
                "priceCurrency": "USD"
            },
            "aggregateRating": {
                "@type": "AggregateRating",
                "ratingValue": "4.7",
                "ratingCount": "87"
            }
        }
    
    def generate_article_schema(
        self,
        title: str,
        description: str,
        author: str,
        date_published: datetime,
        date_modified: Optional[datetime] = None,
        image_url: Optional[str] = None,
        url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate Article schema for blog posts"""
        schema = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title,
            "description": description,
            "author": {
                "@type": "Person",
                "name": author
            },
            "publisher": {
                "@type": "Organization",
                "name": "Successifier",
                "logo": {
                    "@type": "ImageObject",
                    "url": "https://successifier.com/logo.png"
                }
            },
            "datePublished": date_published.isoformat(),
            "dateModified": (date_modified or date_published).isoformat()
        }
        
        if image_url:
            schema["image"] = image_url
        
        if url:
            schema["url"] = url
            schema["mainEntityOfPage"] = {
                "@type": "WebPage",
                "@id": url
            }
        
        return schema
    
    def generate_faq_schema(self, faqs: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Generate FAQ schema
        
        Args:
            faqs: List of {"question": str, "answer": str}
        """
        return {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": faq["question"],
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": faq["answer"]
                    }
                }
                for faq in faqs
            ]
        }
    
    def generate_howto_schema(
        self,
        name: str,
        description: str,
        steps: List[Dict[str, str]],
        total_time: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate HowTo schema for guides
        
        Args:
            name: Guide title
            description: Guide description
            steps: List of {"name": str, "text": str}
            total_time: ISO 8601 duration (e.g., "PT30M" for 30 minutes)
        """
        schema = {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": name,
            "description": description,
            "step": [
                {
                    "@type": "HowToStep",
                    "name": step["name"],
                    "text": step["text"]
                }
                for step in steps
            ]
        }
        
        if total_time:
            schema["totalTime"] = total_time
        
        return schema
    
    def generate_product_schema(
        self,
        name: str,
        description: str,
        rating: float,
        review_count: int,
        price: str = "0",
        currency: str = "USD"
    ) -> Dict[str, Any]:
        """Generate Product schema for software"""
        return {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": name,
            "description": description,
            "brand": {
                "@type": "Brand",
                "name": "Successifier"
            },
            "aggregateRating": {
                "@type": "AggregateRating",
                "ratingValue": str(rating),
                "reviewCount": str(review_count)
            },
            "offers": {
                "@type": "Offer",
                "price": price,
                "priceCurrency": currency,
                "availability": "https://schema.org/InStock"
            }
        }
    
    def generate_breadcrumb_schema(self, breadcrumbs: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Generate BreadcrumbList schema
        
        Args:
            breadcrumbs: List of {"name": str, "url": str}
        """
        return {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": i + 1,
                    "name": crumb["name"],
                    "item": crumb["url"]
                }
                for i, crumb in enumerate(breadcrumbs)
            ]
        }
    
    def generate_video_schema(
        self,
        name: str,
        description: str,
        thumbnail_url: str,
        upload_date: datetime,
        duration: str,
        embed_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate VideoObject schema
        
        Args:
            duration: ISO 8601 duration (e.g., "PT5M30S" for 5:30)
        """
        schema = {
            "@context": "https://schema.org",
            "@type": "VideoObject",
            "name": name,
            "description": description,
            "thumbnailUrl": thumbnail_url,
            "uploadDate": upload_date.isoformat(),
            "duration": duration
        }
        
        if embed_url:
            schema["embedUrl"] = embed_url
        
        return schema
    
    def validate_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate schema markup
        Returns validation results
        """
        try:
            # Basic validation
            if "@context" not in schema:
                return {
                    "valid": False,
                    "errors": ["Missing @context"]
                }
            
            if "@type" not in schema:
                return {
                    "valid": False,
                    "errors": ["Missing @type"]
                }
            
            # Ensure it's valid JSON
            json.dumps(schema)
            
            return {
                "valid": True,
                "errors": []
            }
            
        except Exception as e:
            return {
                "valid": False,
                "errors": [str(e)]
            }
    
    def combine_schemas(self, schemas: List[Dict[str, Any]]) -> str:
        """
        Combine multiple schemas into a single JSON-LD script tag
        """
        if len(schemas) == 1:
            return f'<script type="application/ld+json">\n{json.dumps(schemas[0], indent=2)}\n</script>'
        
        # Multiple schemas - use @graph
        combined = {
            "@context": "https://schema.org",
            "@graph": schemas
        }
        
        return f'<script type="application/ld+json">\n{json.dumps(combined, indent=2)}\n</script>'


# Global instance
schema_generator = SchemaMarkupGenerator()
