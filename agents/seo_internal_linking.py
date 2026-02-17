"""
Internal Linking Optimization
Analyzes content and suggests internal links to improve site structure
"""

from typing import Dict, Any, List, Optional
import re
from collections import defaultdict
import logging

from shared.database import get_supabase

logger = logging.getLogger(__name__)


class InternalLinkingOptimizer:
    """Optimize internal linking structure"""
    
    def __init__(self):
        self.sb = None
        
        # Define content clusters and related keywords
        self.content_clusters = {
            "churn_prevention": {
                "keywords": ["churn", "retention", "customer retention", "reduce churn", "churn rate"],
                "anchor_texts": ["reduce customer churn", "churn prevention strategies", "customer retention"]
            },
            "health_scoring": {
                "keywords": ["health score", "customer health", "health scoring", "account health"],
                "anchor_texts": ["customer health scoring", "health score metrics", "account health tracking"]
            },
            "cs_automation": {
                "keywords": ["automation", "workflow", "playbook", "automated"],
                "anchor_texts": ["CS automation", "automated workflows", "customer success playbooks"]
            },
            "onboarding": {
                "keywords": ["onboarding", "user onboarding", "customer onboarding", "activation"],
                "anchor_texts": ["customer onboarding", "onboarding best practices", "user activation"]
            },
            "expansion": {
                "keywords": ["upsell", "cross-sell", "expansion", "revenue growth"],
                "anchor_texts": ["revenue expansion", "upselling strategies", "customer expansion"]
            },
            "analytics": {
                "keywords": ["analytics", "metrics", "KPI", "reporting"],
                "anchor_texts": ["CS analytics", "customer success metrics", "KPI tracking"]
            }
        }
    
    def _get_sb(self):
        if not self.sb:
            self.sb = get_supabase()
        return self.sb
    
    async def analyze_content_for_links(self, content: str, current_url: str) -> Dict[str, Any]:
        """
        Analyze content and suggest internal links
        
        Args:
            content: The content text to analyze
            current_url: URL of the current page (to avoid self-linking)
        
        Returns:
            Suggested internal links with anchor text and target URLs
        """
        suggestions = []
        
        # Get all published content from database
        sb = self._get_sb()
        result = sb.table("content_library")\
            .select("id, title, url, pillar, target_keyword")\
            .eq("status", "published")\
            .execute()
        
        published_content = result.data if result.data else []
        
        # Analyze content for each cluster
        for cluster_name, cluster_data in self.content_clusters.items():
            for keyword in cluster_data["keywords"]:
                # Check if keyword appears in content
                pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
                matches = pattern.findall(content)
                
                if matches:
                    # Find relevant content to link to
                    for article in published_content:
                        if article.get("url") == current_url:
                            continue  # Skip self-linking
                        
                        # Check if article is in same cluster or related
                        if (article.get("pillar") == cluster_name or 
                            keyword.lower() in article.get("target_keyword", "").lower()):
                            
                            # Suggest link
                            suggestions.append({
                                "keyword": keyword,
                                "target_url": article.get("url"),
                                "target_title": article.get("title"),
                                "anchor_text": self._generate_anchor_text(keyword, cluster_data["anchor_texts"]),
                                "cluster": cluster_name,
                                "relevance_score": self._calculate_relevance(keyword, article)
                            })
        
        # Sort by relevance and deduplicate
        suggestions = sorted(suggestions, key=lambda x: x["relevance_score"], reverse=True)
        suggestions = self._deduplicate_suggestions(suggestions)
        
        return {
            "total_suggestions": len(suggestions),
            "suggestions": suggestions[:10],  # Top 10 suggestions
            "clusters_found": list(set(s["cluster"] for s in suggestions))
        }
    
    def _generate_anchor_text(self, keyword: str, anchor_options: List[str]) -> str:
        """Generate appropriate anchor text"""
        # Use keyword as base, or pick from predefined options
        for option in anchor_options:
            if keyword.lower() in option.lower():
                return option
        return keyword
    
    def _calculate_relevance(self, keyword: str, article: Dict[str, Any]) -> float:
        """Calculate relevance score between keyword and article"""
        score = 0.0
        
        # Exact keyword match in target_keyword
        if keyword.lower() in article.get("target_keyword", "").lower():
            score += 1.0
        
        # Keyword in title
        if keyword.lower() in article.get("title", "").lower():
            score += 0.5
        
        return score
    
    def _deduplicate_suggestions(self, suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate suggestions to same URL"""
        seen_urls = set()
        unique = []
        
        for suggestion in suggestions:
            url = suggestion["target_url"]
            if url not in seen_urls:
                seen_urls.add(url)
                unique.append(suggestion)
        
        return unique
    
    async def generate_link_graph(self) -> Dict[str, Any]:
        """
        Generate site-wide internal linking graph
        Shows which pages link to which
        """
        sb = self._get_sb()
        
        # Get all content
        result = sb.table("content_library")\
            .select("id, title, url, internal_links")\
            .eq("status", "published")\
            .execute()
        
        content = result.data if result.data else []
        
        # Build graph
        graph = defaultdict(list)
        incoming_links = defaultdict(int)
        
        for article in content:
            url = article.get("url")
            links = article.get("internal_links", [])
            
            for link in links:
                graph[url].append(link)
                incoming_links[link] += 1
        
        # Find orphan pages (no incoming links)
        all_urls = {a.get("url") for a in content}
        orphan_pages = [url for url in all_urls if incoming_links[url] == 0]
        
        # Find hub pages (many outgoing links)
        hub_pages = sorted(
            [(url, len(links)) for url, links in graph.items()],
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        # Find authority pages (many incoming links)
        authority_pages = sorted(
            incoming_links.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        return {
            "total_pages": len(content),
            "total_internal_links": sum(len(links) for links in graph.values()),
            "avg_links_per_page": sum(len(links) for links in graph.values()) / len(content) if content else 0,
            "orphan_pages": orphan_pages,
            "hub_pages": [{"url": url, "outgoing_links": count} for url, count in hub_pages],
            "authority_pages": [{"url": url, "incoming_links": count} for url, count in authority_pages]
        }
    
    async def suggest_pillar_page_links(self, pillar: str) -> List[Dict[str, Any]]:
        """
        Suggest which supporting articles should link to pillar page
        
        Args:
            pillar: Content pillar name
        """
        sb = self._get_sb()
        
        # Get pillar page
        pillar_result = sb.table("content_library")\
            .select("*")\
            .eq("pillar", pillar)\
            .eq("type", "pillar_page")\
            .limit(1)\
            .execute()
        
        if not pillar_result.data:
            return []
        
        pillar_page = pillar_result.data[0]
        
        # Get supporting articles in same pillar
        supporting_result = sb.table("content_library")\
            .select("*")\
            .eq("pillar", pillar)\
            .neq("type", "pillar_page")\
            .eq("status", "published")\
            .execute()
        
        supporting_articles = supporting_result.data if supporting_result.data else []
        
        suggestions = []
        for article in supporting_articles:
            # Check if already links to pillar
            existing_links = article.get("internal_links", [])
            if pillar_page.get("url") not in existing_links:
                suggestions.append({
                    "article_url": article.get("url"),
                    "article_title": article.get("title"),
                    "pillar_url": pillar_page.get("url"),
                    "pillar_title": pillar_page.get("title"),
                    "suggested_anchor": f"Learn more about {pillar.replace('_', ' ')}"
                })
        
        return suggestions


# Global instance
internal_linking_optimizer = InternalLinkingOptimizer()
