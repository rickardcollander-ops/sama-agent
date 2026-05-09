"""
Internal Linking Optimization
Analyzes content and suggests internal links to improve site structure.

Two link sources are merged:
  * ``content`` table — articles authored by SAMA (canonical url_path).
  * ``external_pages`` table — URLs discovered from the tenant's sitemap
    (legacy posts, product pages, docs). See agents/external_pages.py.

Two scoring layers are applied:
  * Lexical — keyword/cluster regex match in the source content.
  * Semantic — cosine similarity between the source content embedding and
    the candidate page embedding (when Voyage is configured). Falls back
    silently to lexical-only when not.
"""

from typing import Dict, Any, List, Optional, Sequence
import re
from collections import defaultdict
import logging

from shared.database import get_supabase, run_db
from shared.embeddings import (
    cosine_similarity,
    embed_text,
    is_configured as embeddings_configured,
)
from .models import EXTERNAL_PAGES_TABLE

logger = logging.getLogger(__name__)

CONTENT_TABLE = "content"

# Cosine similarity below this is treated as "not related" and the candidate
# is dropped even if it had a weak keyword match. Calibrated against
# voyage-3 embeddings on SaaS marketing copy; tune downward if recall is
# too tight on multilingual sites.
SEMANTIC_MIN_SIMILARITY = 0.55
# Weight applied to the cosine score when blending into final relevance.
SEMANTIC_WEIGHT = 2.0


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

    async def _load_link_pool(self, tenant_id: Optional[str]) -> List[Dict[str, Any]]:
        """Merge SAMA-authored ``content`` rows with sitemap-discovered
        ``external_pages``. Returns a uniform schema:
            { url, title, target_keyword, source, embedding }
        Both feeds are de-duped on URL with ``content`` taking precedence."""
        sb = self._get_sb()

        content_rows = await run_db(
            lambda: sb.table(CONTENT_TABLE)
            .select("id, title, url_path, content_type, target_keyword")
            .eq("status", "published")
            .execute()
        )
        content_data = content_rows.data or []

        external_data: List[Dict[str, Any]] = []
        try:
            def _fetch_external():
                q = sb.table(EXTERNAL_PAGES_TABLE).select(
                    "url, title, description, h1, embedding"
                )
                if tenant_id:
                    q = q.eq("tenant_id", tenant_id)
                return q.execute()
            external_rows = await run_db(_fetch_external)
            external_data = external_rows.data or []
        except Exception as e:
            # Table may not exist yet on installs that haven't run migration 044.
            logger.debug("external_pages unavailable: %s", e)

        pool: List[Dict[str, Any]] = []
        seen: set = set()
        for row in content_data:
            url = row.get("url_path") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            pool.append({
                "url": url,
                "title": row.get("title") or "",
                "target_keyword": row.get("target_keyword") or "",
                "source": "content",
                "embedding": None,
            })
        for row in external_data:
            url = row.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            pool.append({
                "url": url,
                "title": row.get("title") or row.get("h1") or "",
                "target_keyword": row.get("h1") or row.get("description") or "",
                "source": "external",
                "embedding": row.get("embedding"),
            })
        return pool

    async def analyze_content_for_links(
        self,
        content: str,
        current_url: str,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Suggest internal links for ``content``. Combines lexical cluster
        matching with optional semantic similarity (Voyage). The current URL
        is excluded so authors don't self-link."""
        pool = await self._load_link_pool(tenant_id)

        # ── Lexical pass ─────────────────────────────────────────────────
        suggestions: List[Dict[str, Any]] = []
        for cluster_name, cluster_data in self.content_clusters.items():
            for keyword in cluster_data["keywords"]:
                pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
                if not pattern.search(content):
                    continue

                for article in pool:
                    article_url = article.get("url") or ""
                    if article_url == current_url:
                        continue

                    target_kw = article.get("target_keyword") or ""
                    title = article.get("title") or ""

                    if keyword.lower() in target_kw.lower() or keyword.lower() in title.lower():
                        score = self._calculate_relevance(keyword, article)
                        suggestions.append({
                            "keyword": keyword,
                            "target_url": article_url,
                            "target_title": title,
                            "anchor_text": self._generate_anchor_text(keyword, cluster_data["anchor_texts"]),
                            "cluster": cluster_name,
                            "source": article.get("source", "content"),
                            "lexical_score": score,
                            "semantic_score": 0.0,
                            "relevance_score": score,
                        })

        # ── Semantic pass ────────────────────────────────────────────────
        # Pull every candidate that has a stored embedding and rank against
        # the source content. This catches related pages whose anchor terms
        # don't appear verbatim in the new article (e.g. "QBR" vs "quarterly
        # business review"). Only candidates from external_pages currently
        # carry embeddings; if the SAMA content table grows embeddings later
        # the same loop will pick them up.
        if embeddings_configured():
            candidates_with_emb = [
                a for a in pool
                if a.get("embedding") and a.get("url") and a["url"] != current_url
            ]
            if candidates_with_emb:
                source_emb = await embed_text(content[:4000], input_type="query")
                if source_emb:
                    semantic_hits = self._rank_semantic(source_emb, candidates_with_emb)
                    suggestions = self._merge_semantic(suggestions, semantic_hits)

        suggestions = sorted(suggestions, key=lambda x: x["relevance_score"], reverse=True)
        suggestions = self._deduplicate_suggestions(suggestions)

        return {
            "total_suggestions": len(suggestions),
            "suggestions": suggestions[:10],
            "clusters_found": list({s["cluster"] for s in suggestions if s.get("cluster")}),
            "semantic_enabled": embeddings_configured(),
        }

    def _rank_semantic(
        self,
        source_emb: Sequence[float],
        candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        for cand in candidates:
            sim = cosine_similarity(source_emb, cand["embedding"])
            if sim < SEMANTIC_MIN_SIMILARITY:
                continue
            ranked.append({
                "keyword": "",
                "target_url": cand["url"],
                "target_title": cand.get("title") or "",
                "anchor_text": cand.get("title") or cand.get("target_keyword") or "related article",
                "cluster": "semantic",
                "source": cand.get("source", "external"),
                "lexical_score": 0.0,
                "semantic_score": sim,
                "relevance_score": SEMANTIC_WEIGHT * sim,
            })
        return ranked

    @staticmethod
    def _merge_semantic(
        lexical: List[Dict[str, Any]],
        semantic: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Boost lexical hits that also win on similarity, and append the
        rest as semantic-only suggestions."""
        by_url = {s["target_url"]: s for s in lexical}
        for hit in semantic:
            existing = by_url.get(hit["target_url"])
            if existing:
                existing["semantic_score"] = hit["semantic_score"]
                existing["relevance_score"] = (
                    existing["lexical_score"] + SEMANTIC_WEIGHT * hit["semantic_score"]
                )
            else:
                lexical.append(hit)
                by_url[hit["target_url"]] = hit
        return lexical

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
