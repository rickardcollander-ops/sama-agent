"""
Premium article writer.

Produces a structured payload (TOC, key takeaways, H2/H3 sections, FAQ,
internal/external link targets) plus a hybrid image set, computes a
heuristic SEO score, and assembles a polished markdown article ready for
the dashboard's article view.

Pipeline:

    1. Ask Claude for a strict JSON outline + section bodies.
    2. Resolve images (1 generated featured + N stock inline) in parallel.
    3. Inject internal links via :mod:`agents.seo_internal_linking`.
    4. Render the final markdown (TOC -> Key Takeaways table -> sections
       with inline images -> FAQ).
    5. Score the result via :mod:`agents.article_score`.
    6. Return everything packed for storage in ``content_pieces``
       (top-level columns + ``article_data`` JSONB blob).

The function never raises on imagery / linking failures — those degrade
quietly so a missing API key or rate-limit doesn't break drafting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from anthropic import Anthropic

from shared.config import settings
from .article_images import (
    fetch_inline_images,
    generate_featured_image,
    upload_featured_to_supabase,
)
from .article_score import compute_article_score


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# JSON outline prompt
# ──────────────────────────────────────────────────────────────────────────


_OUTLINE_SYSTEM = """You are a senior B2B SaaS content strategist and SEO writer.
You produce in-depth, expert articles that read like the best long-form
posts from Ahrefs, Backlinko, and First Round Review — concrete, useful,
and visibly structured (TOC, key takeaways, H2/H3 sections, comparison
tables where helpful, FAQ).

You ALWAYS respond with a single valid JSON object — no prose, no
markdown fences. Every section body is itself markdown. Use proper
sentence punctuation. Avoid filler and avoid the word "delve"."""


def _outline_prompt(
    *,
    title: str,
    topic: str,
    primary_keyword: str,
    pillar: str,
    word_count_target: int,
    inline_image_slots: int,
) -> str:
    return f"""Plan and write a comprehensive article.

Title (may refine): {title}
Topic: {topic}
Primary keyword: {primary_keyword or '(infer from title)'}
Content pillar: {pillar or '(general)'}
Target length: about {word_count_target} words across all sections.

Return ONLY a JSON object with this exact shape:

{{
  "title": "Final article title (concise, includes primary keyword)",
  "slug": "kebab-case-url-slug-no-stopwords",
  "meta_title": "<= 60 chars, includes primary keyword",
  "meta_description": "130-160 chars, value prop + primary keyword",
  "primary_keyword": "the main target keyword",
  "secondary_keywords": ["3-6 supporting keywords"],
  "intro_md": "2-3 paragraph hook in markdown. No heading.",
  "table_of_contents": [
    {{"id": "kebab-section-id", "label": "Section heading as shown in TOC"}}
  ],
  "key_takeaways": [
    {{"point": "Short label (3-5 words)", "details": "One-sentence explanation"}}
  ],
  "sections": [
    {{
      "id": "kebab-section-id (matches TOC)",
      "heading": "H2 heading",
      "image_query": "concrete photo search query (e.g. 'team analyzing dashboards in modern office'); MAX {inline_image_slots} sections may include this; others omit it",
      "body_md": "Full section body in markdown. Use H3 (###) sub-headings, bullet lists, blockquotes, and at least one comparison or summary table somewhere in the article. Reference internal_link_anchors and external_link_anchors naturally as plain phrases — DO NOT insert links yourself.",
      "internal_link_anchors": ["2-4 short phrases that should later become links to related published articles"],
      "external_link_anchors": [
        {{"phrase": "exact phrase to wrap as a link", "url": "https://reputable-source.example.com/path", "reason": "why this source supports the claim"}}
      ]
    }}
  ],
  "faq": [
    {{"q": "Question?", "a": "1-3 sentence answer in markdown."}}
  ]
}}

Requirements:
  - At least 5 sections, each 200-450 words.
  - At least 4 key_takeaways.
  - At least 3 FAQ entries.
  - Include at least one markdown table inside one section's body_md.
  - external_link_anchors must point to real, well-known, evergreen sources
    (industry reports, vendor docs, established media). Never invent URLs.
  - Keep tone confident, specific, and free of hype words.
"""


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9\s-]", "", (text or "").lower()).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80] or "article"


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _parse_outline(raw: str) -> Dict[str, Any]:
    text = _strip_json_fence(raw)
    return json.loads(text)


# ──────────────────────────────────────────────────────────────────────────
# Internal & external link injection
# ──────────────────────────────────────────────────────────────────────────


def _inject_internal_links(
    body_md: str,
    anchors: List[str],
    candidates: List[Dict[str, Any]],
) -> tuple[str, int]:
    """For each anchor phrase, try to wrap its first plain-text occurrence
    in a markdown link to a matching published piece. Returns the modified
    body and the number of links inserted.

    A "match" is a candidate whose title or target_keyword shares at least
    one significant word with the anchor. Cheap but works well for a small
    site corpus; can swap in vector matching later.
    """
    if not anchors or not candidates:
        return body_md, 0

    inserted = 0
    used_targets: set[str] = set()

    def _score(anchor: str, cand: Dict[str, Any]) -> int:
        anchor_words = {w for w in re.findall(r"\w+", anchor.lower()) if len(w) > 3}
        haystack = " ".join([
            (cand.get("title") or ""),
            (cand.get("target_keyword") or ""),
        ]).lower()
        return sum(1 for w in anchor_words if w in haystack)

    for anchor in anchors:
        anchor = (anchor or "").strip()
        if not anchor:
            continue
        ranked = sorted(
            (c for c in candidates if c.get("id") not in used_targets),
            key=lambda c: _score(anchor, c),
            reverse=True,
        )
        if not ranked or _score(anchor, ranked[0]) == 0:
            continue
        target = ranked[0]
        url = target.get("url_path") or f"/blog/{target.get('id')}"
        # Replace only the first standalone occurrence of the anchor that
        # isn't already inside a markdown link.
        pattern = re.compile(
            rf"(?<!\[)(?<!\]\(){re.escape(anchor)}(?![^\[]*\])",
            flags=re.IGNORECASE,
        )
        new_body, n = pattern.subn(f"[{anchor}]({url})", body_md, count=1)
        if n:
            body_md = new_body
            used_targets.add(target.get("id"))
            inserted += 1
    return body_md, inserted


def _inject_external_links(body_md: str, anchors: List[Dict[str, Any]]) -> tuple[str, int]:
    inserted = 0
    for entry in anchors or []:
        phrase = (entry.get("phrase") or "").strip()
        url = (entry.get("url") or "").strip()
        if not phrase or not url.startswith(("http://", "https://")):
            continue
        pattern = re.compile(
            rf"(?<!\[)(?<!\]\(){re.escape(phrase)}(?![^\[]*\])",
            flags=re.IGNORECASE,
        )
        new_body, n = pattern.subn(f"[{phrase}]({url})", body_md, count=1)
        if n:
            body_md = new_body
            inserted += 1
    return body_md, inserted


# ──────────────────────────────────────────────────────────────────────────
# Markdown assembly
# ──────────────────────────────────────────────────────────────────────────


def _render_markdown(outline: Dict[str, Any]) -> str:
    """Assemble the rendered markdown the dashboard will display.

    The dashboard ALSO consumes the structured payload (article_data) for
    the right-hand panel, but it renders this markdown body for the
    main article column.
    """
    parts: List[str] = []
    title = outline.get("title", "Untitled")
    parts.append(f"# {title}\n")

    intro = (outline.get("intro_md") or "").strip()
    if intro:
        parts.append(intro + "\n")

    # Table of contents
    toc = outline.get("table_of_contents") or []
    if toc:
        parts.append("## Table of Contents\n")
        for entry in toc:
            label = entry.get("label", "")
            anchor = entry.get("id") or _slugify(label)
            parts.append(f"- [{label}](#{anchor})")
        parts.append("")

    # Key takeaways table
    takeaways = outline.get("key_takeaways") or []
    if takeaways:
        parts.append("## Key Takeaways\n")
        parts.append("| Point | Details |")
        parts.append("| --- | --- |")
        for t in takeaways:
            point = (t.get("point") or "").replace("|", "\\|")
            details = (t.get("details") or "").replace("|", "\\|")
            parts.append(f"| {point} | {details} |")
        parts.append("")

    # Sections
    for section in outline.get("sections") or []:
        heading = section.get("heading") or ""
        anchor = section.get("id") or _slugify(heading)
        parts.append(f"## {heading} {{#{anchor}}}\n")
        image = section.get("image")
        if image and image.get("url"):
            alt = (image.get("alt") or heading).replace("]", "")
            parts.append(f"![{alt}]({image['url']})")
            credit = image.get("credit")
            if credit:
                parts.append(f"_{credit}_")
            parts.append("")
        body = (section.get("body_md") or "").strip()
        if body:
            parts.append(body + "\n")

    # FAQ
    faq = outline.get("faq") or []
    if faq:
        parts.append("## Frequently Asked Questions\n")
        for entry in faq:
            q = entry.get("q") or ""
            a = entry.get("a") or ""
            parts.append(f"### {q}\n")
            parts.append(a.strip() + "\n")

    return "\n".join(parts).strip() + "\n"


# ──────────────────────────────────────────────────────────────────────────
# Internal-link candidate fetch
# ──────────────────────────────────────────────────────────────────────────


def _fetch_internal_link_candidates(tenant_id: str, exclude_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return published pieces this article can link to. Uses the same
    table the rest of the agent writes to (``content_pieces``); falls
    back to an empty list on any error so drafting still proceeds.
    """
    try:
        from shared.database import get_supabase
    except Exception:
        return []
    try:
        sb = get_supabase()
        q = (
            sb.table("content_pieces")
            .select("id, title, slug, target_keyword, target_url")
            .eq("tenant_id", tenant_id)
            .eq("status", "published")
            .limit(200)
        )
        rows = (q.execute().data or [])
        candidates: List[Dict[str, Any]] = []
        for row in rows:
            if exclude_id and row.get("id") == exclude_id:
                continue
            slug = row.get("slug")
            url_path = row.get("target_url") or (f"/blog/{slug}" if slug else f"/blog/{row.get('id')}")
            candidates.append({
                "id": row.get("id"),
                "title": row.get("title"),
                "target_keyword": row.get("target_keyword"),
                "url_path": url_path,
            })
        return candidates
    except Exception as exc:  # noqa: BLE001
        logger.info("Internal-link candidate fetch skipped: %s", exc)
        return []


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────


async def generate_premium_article(
    *,
    title: str,
    topic: str = "",
    primary_keyword: str = "",
    pillar: str = "",
    tenant_id: str = "default",
    word_count_target: int = 2000,
    inline_image_slots: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate a fully-structured article and return a payload ready to
    persist into ``content_pieces``.

    Returned shape::

        {
          "title": str,
          "slug": str,
          "meta_title": str,
          "meta_description": str,
          "content": str,             # final assembled markdown
          "word_count": int,
          "featured_image_url": str | None,
          "featured_image_alt": str | None,
          "article_score": int,
          "article_data": {
            "primary_keyword": str,
            "secondary_keywords": [..],
            "table_of_contents": [..],
            "key_takeaways": [..],
            "sections": [..],          # echo of outline incl. resolved images
            "faq": [..],
            "featured_image": {..} | None,
            "score": {"score", "rules", "suggestions", "metrics"},
            "stats": {
              "internal_links_inserted": int,
              "external_links_inserted": int,
              "image_count": int,
              "generated_at": iso8601,
            }
          }
        }
    """
    inline_slots = settings.PREMIUM_ARTICLE_INLINE_IMAGES if inline_image_slots is None else inline_image_slots

    # 1. Outline + bodies from Claude ────────────────────────────────────
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    user_prompt = _outline_prompt(
        title=title,
        topic=topic,
        primary_keyword=primary_keyword,
        pillar=pillar,
        word_count_target=word_count_target,
        inline_image_slots=inline_slots,
    )
    message = await asyncio.to_thread(
        client.messages.create,
        model=settings.CLAUDE_MODEL,
        max_tokens=8000,
        system=_OUTLINE_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = message.content[0].text
    try:
        outline = _parse_outline(raw)
    except json.JSONDecodeError as exc:
        logger.error("Outline JSON parse failed: %s -- raw=%s", exc, raw[:400])
        raise

    # Normalise required fields with safe defaults.
    outline.setdefault("title", title)
    outline.setdefault("slug", _slugify(outline["title"]))
    outline.setdefault("primary_keyword", primary_keyword or outline.get("primary_keyword", ""))

    # 2. Imagery (parallel: featured + inline slots) ─────────────────────
    sections = outline.get("sections") or []
    inline_queries: List[tuple[int, str]] = []
    for idx, sec in enumerate(sections):
        q = (sec.get("image_query") or "").strip()
        if q and len(inline_queries) < inline_slots:
            inline_queries.append((idx, q))

    upload_prefix = f"{tenant_id}/{outline.get('slug') or _slugify(outline['title'])}"
    featured_task = generate_featured_image(
        title=outline["title"],
        summary=outline.get("meta_description") or topic or outline["title"],
        primary_keyword=outline.get("primary_keyword", ""),
    )
    inline_task = fetch_inline_images(
        [q for _, q in inline_queries],
        upload_path_prefix=upload_prefix,
    )
    featured, inline_images = await asyncio.gather(featured_task, inline_task)

    # Featured image: try to upload to Supabase Storage; fall back to
    # inlining a data URL into article_data so the dashboard still has
    # something to render.
    featured_image_url: Optional[str] = None
    featured_image_alt: Optional[str] = None
    if featured:
        featured_image_alt = featured.get("alt")
        slug = outline["slug"]
        path = f"{tenant_id}/{slug}-{int(datetime.now(timezone.utc).timestamp())}.png"
        hosted = upload_featured_to_supabase(b64_png=featured["b64"], path=path)
        featured_image_url = hosted or featured.get("data_url")
        outline["featured_image"] = {
            "url": featured_image_url,
            "alt": featured_image_alt,
            "source": featured.get("source"),
            "hosted": bool(hosted),
        }
    else:
        outline["featured_image"] = None

    # Attach inline images back onto their sections.
    for (idx, _q), img in zip(inline_queries, inline_images):
        if img and img.get("url"):
            sections[idx]["image"] = img

    image_count = (1 if featured_image_url else 0) + sum(
        1 for s in sections if (s.get("image") or {}).get("url")
    )

    # 3. Inject internal + external links into each section body. ────────
    candidates = _fetch_internal_link_candidates(tenant_id)
    internal_inserted = 0
    external_inserted = 0
    for sec in sections:
        body = sec.get("body_md") or ""
        body, n_int = _inject_internal_links(body, sec.get("internal_link_anchors") or [], candidates)
        body, n_ext = _inject_external_links(body, sec.get("external_link_anchors") or [])
        sec["body_md"] = body
        internal_inserted += n_int
        external_inserted += n_ext

    # 4. Final markdown ──────────────────────────────────────────────────
    markdown = _render_markdown(outline)
    word_count = len(re.findall(r"\b\w+\b", markdown))

    # 5. Score ───────────────────────────────────────────────────────────
    score = compute_article_score(
        markdown=markdown,
        meta_description=outline.get("meta_description", ""),
        primary_keyword=outline.get("primary_keyword", ""),
        table_of_contents=outline.get("table_of_contents"),
        key_takeaways=outline.get("key_takeaways"),
        faq=outline.get("faq"),
        image_count=image_count,
        internal_domain=settings.SUCCESSIFIER_DOMAIN,
    )

    article_data = {
        "primary_keyword": outline.get("primary_keyword", ""),
        "secondary_keywords": outline.get("secondary_keywords", []),
        "table_of_contents": outline.get("table_of_contents", []),
        "key_takeaways": outline.get("key_takeaways", []),
        "sections": sections,
        "faq": outline.get("faq", []),
        "featured_image": outline.get("featured_image"),
        "score": score,
        "stats": {
            "internal_links_inserted": internal_inserted,
            "external_links_inserted": external_inserted,
            "image_count": image_count,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": settings.CLAUDE_MODEL,
        },
    }

    return {
        "title": outline["title"],
        "slug": outline["slug"],
        "meta_title": outline.get("meta_title", outline["title"])[:60],
        "meta_description": outline.get("meta_description", ""),
        "content": markdown,
        "word_count": word_count,
        "featured_image_url": featured_image_url,
        "featured_image_alt": featured_image_alt,
        "article_score": score["score"],
        "article_data": article_data,
    }
