"""
Internal Linking Optimization
Analyzes content and suggests internal links to improve site structure.

Uses the 'content' table (same as ContentAgent) with columns:
  id, title, url_path, content_type, target_keyword, status
"""

from typing import Dict, Any, List, Optional
import re
from collections import defaultdict
import logging

from shared.database import get_supabase

logger = logging.getLogger(__name__)

CONTENT_TABLE = "content"


class InternalLinkingOptimizer:
    """Optimize internal linking structure"""

    def __init__(self):
        self.sb = None

        # Content clusters with associated keywords and anchor text options
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
        Analyze content and suggest internal links.

        Args:
            content: The content text to analyze
            current_url: URL path of the current page (to avoid self-linking)

        Returns:
            Suggested internal links with anchor text and target URLs
        """
        suggestions = []
        sb = self._get_sb()

        # Fetch published content from the actual table used by ContentAgent
        result = (
            sb.table(CONTENT_TABLE)
            .select("id, title, url_path, content_type, target_keyword")
            .eq("status", "published")
            .execute()
        )
        published_content = result.data or []

        for cluster_name, cluster_data in self.content_clusters.items():
            for keyword in cluster_data["keywords"]:
                pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
                if not pattern.search(content):
                    continue

                for article in published_content:
                    article_url = article.get("url_path") or ""
                    if article_url == current_url:
                        continue  # avoid self-linking

                    target_kw = article.get("target_keyword") or ""
                    title = article.get("title") or ""

                    if keyword.lower() in target_kw.lower() or keyword.lower() in title.lower():
                        suggestions.append({
                            "keyword": keyword,
                            "target_url": article_url,
                            "target_title": title,
                            "anchor_text": self._generate_anchor_text(keyword, cluster_data["anchor_texts"]),
                            "cluster": cluster_name,
                            "relevance_score": self._calculate_relevance(keyword, article)
                        })

        suggestions = sorted(suggestions, key=lambda x: x["relevance_score"], reverse=True)
        suggestions = self._deduplicate_suggestions(suggestions)

        return {
            "total_suggestions": len(suggestions),
            "suggestions": suggestions[:10],
            "clusters_found": list(set(s["cluster"] for s in suggestions))
        }

    def _generate_anchor_text(self, keyword: str, anchor_options: List[str]) -> str:
        for option in anchor_options:
            if keyword.lower() in option.lower():
                return option
        return keyword

    def _calculate_relevance(self, keyword: str, article: Dict[str, Any]) -> float:
        score = 0.0
        if keyword.lower() in (article.get("target_keyword") or "").lower():
            score += 1.0
        if keyword.lower() in (article.get("title") or "").lower():
            score += 0.5
        return score

    def _deduplicate_suggestions(self, suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen_urls: set = set()
        unique = []
        for suggestion in suggestions:
            url = suggestion["target_url"]
            if url not in seen_urls:
                seen_urls.add(url)
                unique.append(suggestion)
        return unique

    async def generate_link_graph(self) -> Dict[str, Any]:
        """
        Generate site-wide internal linking graph based on content in the database.
        Since content rows don't store explicit internal_links, we infer connections
        via shared keywords/clusters.
        """
        sb = self._get_sb()
        result = (
            sb.table(CONTENT_TABLE)
            .select("id, title, url_path, target_keyword, content_type")
            .eq("status", "published")
            .execute()
        )
        content = result.data or []

        if not content:
            return {
                "total_pages": 0,
                "total_internal_links": 0,
                "avg_links_per_page": 0,
                "orphan_pages": [],
                "hub_pages": [],
                "authority_pages": []
            }

        # Build keyword → articles index
        kw_to_articles: Dict[str, List[str]] = defaultdict(list)
        for article in content:
            url = article.get("url_path") or ""
            kw = (article.get("target_keyword") or "").lower()
            if kw and url:
                kw_to_articles[kw].append(url)

        # Build graph: articles that share keyword clusters link to each other
        graph: Dict[str, List[str]] = defaultdict(list)
        incoming: Dict[str, int] = defaultdict(int)
        all_urls = {a.get("url_path") for a in content if a.get("url_path")}

        for cluster_data in self.content_clusters.values():
            matching_urls = []
            for kw in cluster_data["keywords"]:
                for url in kw_to_articles.get(kw, []):
                    if url not in matching_urls:
                        matching_urls.append(url)
            # Each matching URL can link to all others in the cluster
            for url in matching_urls:
                for target in matching_urls:
                    if target != url and target not in graph[url]:
                        graph[url].append(target)
                        incoming[target] += 1

        orphan_pages = [url for url in all_urls if incoming[url] == 0]
        hub_pages = sorted(
            [(url, len(links)) for url, links in graph.items()],
            key=lambda x: x[1], reverse=True
        )[:10]
        authority_pages = sorted(incoming.items(), key=lambda x: x[1], reverse=True)[:10]
        total_links = sum(len(v) for v in graph.values())

        return {
            "total_pages": len(content),
            "total_internal_links": total_links,
            "avg_links_per_page": round(total_links / len(content), 1) if content else 0,
            "orphan_pages": list(orphan_pages),
            "hub_pages": [{"url": u, "outgoing_links": c} for u, c in hub_pages],
            "authority_pages": [{"url": u, "incoming_links": c} for u, c in authority_pages]
        }

    async def suggest_pillar_page_links(self, pillar: str) -> List[Dict[str, Any]]:
        """
        Suggest which supporting articles should link to a pillar page.
        Uses content_type='pillar_page' to identify pillar articles.
        """
        sb = self._get_sb()

        # Find pillar page — content_type == 'pillar_page' and keyword matches pillar name
        pillar_result = (
            sb.table(CONTENT_TABLE)
            .select("*")
            .eq("content_type", "pillar_page")
            .execute()
        )
        pillar_rows = pillar_result.data or []
        # Match by pillar name in title or target_keyword
        pillar_page = next(
            (r for r in pillar_rows if pillar.lower().replace("_", " ") in (r.get("title") or "").lower()
             or pillar.lower() in (r.get("target_keyword") or "").lower()),
            None
        )
        if not pillar_page:
            return []

        pillar_url = pillar_page.get("url_path", "")

        # Get supporting articles whose target keyword relates to this pillar
        cluster = self.content_clusters.get(pillar, {})
        cluster_keywords = cluster.get("keywords", [])

        supporting_result = (
            sb.table(CONTENT_TABLE)
            .select("id, title, url_path, target_keyword")
            .eq("status", "published")
            .execute()
        )
        supporting_articles = supporting_result.data or []

        suggestions = []
        for article in supporting_articles:
            if article.get("url_path") == pillar_url:
                continue
            target_kw = (article.get("target_keyword") or "").lower()
            if any(kw in target_kw for kw in cluster_keywords):
                suggestions.append({
                    "article_url": article.get("url_path"),
                    "article_title": article.get("title"),
                    "pillar_url": pillar_url,
                    "pillar_title": pillar_page.get("title"),
                    "suggested_anchor": f"Learn more about {pillar.replace('_', ' ')}"
                })

        return suggestions


# Global instance
internal_linking_optimizer = InternalLinkingOptimizer()
