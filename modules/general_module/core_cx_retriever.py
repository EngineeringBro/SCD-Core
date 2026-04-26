"""
CX Retriever — Step 1 of the General Module pipeline.

Fetches 10-50 candidate reference cases from:
  1. Closed SCD Jira tickets  (JQL full-text search)
  2. JSM Knowledge Base articles (Confluence REST API)

Returns a list of Candidate dataclass objects with no scoring applied.
Scoring is done by cx_reranker.
"""
from __future__ import annotations
import gzip
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Candidate:
    source: str          # "jira_closed" | "kb_article"
    ref_id: str          # Jira ticket key or Confluence article ID
    title: str
    body: str            # description + resolution excerpt, or article body
    url: str
    metadata: dict = field(default_factory=dict)  # topic, root_cause, resolution, etc.


_STOP_WORDS = {
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for",
    "of", "and", "or", "but", "not", "with", "this", "that", "was",
    "are", "be", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "i", "we",
    "you", "he", "she", "they", "my", "our", "your", "their", "its",
    "from", "by", "as", "if", "so", "no", "yes", "hi", "hello",
    "please", "thank", "thanks", "regards", "dear",
}


def _extract_adf_text(node: dict | list | str, _depth: int = 0) -> str:
    """Recursively extract plain text from Atlassian Document Format."""
    if _depth > 10:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(_extract_adf_text(child, _depth + 1) for child in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = []
        for v in node.values():
            if isinstance(v, (dict, list)):
                parts.append(_extract_adf_text(v, _depth + 1))
        return " ".join(parts)
    return ""


def _extract_keywords(ticket: dict, max_keywords: int = 8) -> list[str]:
    """Pull the most distinctive words from summary + topic + description."""
    fields = ticket.get("fields", {})
    summary = fields.get("summary", "")
    topic = (fields.get("customfield_10170") or {}).get("value", "")

    desc_raw = fields.get("description") or {}
    desc_text = _extract_adf_text(desc_raw) if isinstance(desc_raw, dict) else str(desc_raw)

    raw = f"{summary} {topic} {desc_text[:500]}"
    tokens = re.findall(r"[a-zA-Z]{3,}", raw.lower())
    keywords = [t for t in tokens if t not in _STOP_WORDS]

    freq: dict[str, int] = {}
    for k in keywords:
        freq[k] = freq.get(k, 0) + 1
    ranked = sorted(freq, key=lambda k: freq[k], reverse=True)
    return ranked[:max_keywords]


def retrieve(ticket: dict, jira) -> list[Candidate]:
    """
    Main entry point. Returns up to 50 candidates from all sources.
    Never raises — returns empty list on failure.
    """
    keywords = _extract_keywords(ticket)
    if not keywords:
        print("[cx_retriever] No keywords extracted — skipping retrieval")
        return []

    candidates: list[Candidate] = []

    # Source 1: Local ticket cache (fast, no API call)
    try:
        cache_candidates = _fetch_from_cache(keywords, ticket.get("key", ""))
        candidates.extend(cache_candidates)
        print(f"[cx_retriever] Cache hit: {len(cache_candidates)} candidates")
    except Exception as exc:
        print(f"[cx_retriever] Cache read failed (will fall back to live API): {exc}")

    # Source 2: Live Jira API for closed tickets (fallback or supplement)
    if len(candidates) < 5:
        try:
            jira_candidates = _fetch_jira_closed(jira, keywords, ticket.get("key", ""))
            # Deduplicate by ref_id
            existing_ids = {c.ref_id for c in candidates}
            new = [c for c in jira_candidates if c.ref_id not in existing_ids]
            candidates.extend(new)
        except Exception as exc:
            print(f"[cx_retriever] Jira closed-ticket search failed: {exc}")

    # Source 3: JSM Knowledge Base articles
    try:
        kb_candidates = _fetch_kb_articles(jira, keywords)
        candidates.extend(kb_candidates)
    except Exception as exc:
        print(f"[cx_retriever] KB article search failed: {exc}")

    n_jira = sum(1 for c in candidates if c.source == "jira_closed")
    n_kb = sum(1 for c in candidates if c.source == "kb_article")
    print(f"[cx_retriever] Retrieved {len(candidates)} candidates ({n_jira} jira, {n_kb} kb)")
    return candidates[:50]


_CACHE_PATH = Path("knowledge/tickets_cache.jsonl.gz")


def _fetch_from_cache(keywords: list[str], exclude_key: str) -> list[Candidate]:
    """
    Search the local gzip JSONL cache for tickets matching keywords.
    Returns up to 30 candidates. Returns [] if cache doesn't exist yet.
    """
    if not _CACHE_PATH.exists():
        return []

    kw_set = {k.lower() for k in keywords}
    matches: list[Candidate] = []

    with gzip.open(_CACHE_PATH, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            key = rec.get("key", "")
            if key == exclude_key:
                continue

            # Score: count keyword hits across searchable fields
            searchable = " ".join([
                rec.get("summary", ""),
                rec.get("description", ""),
                rec.get("topic", ""),
                rec.get("resolution", ""),
                " ".join(rec.get("comments", [])),
            ]).lower()

            hits = sum(1 for kw in kw_set if kw in searchable)
            if hits == 0:
                continue

            body = (
                f"Description: {rec.get('description', '')[:400]}\n"
                f"Resolution: {rec.get('resolution', '')}\n"
                f"Comments: {' | '.join(rec.get('comments', [])[:3])}"
            )

            matches.append((hits, Candidate(
                source="jira_closed",
                ref_id=key,
                title=rec.get("summary", ""),
                body=body,
                url=f"https://servicecentral.atlassian.net/browse/{key}",
                metadata={
                    "topic": rec.get("topic", ""),
                    "root_cause": rec.get("root_cause", ""),
                    "resolution": rec.get("resolution", ""),
                },
            )))

    # Sort by hit count descending, return top 30
    matches.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in matches[:30]]


def _fetch_jira_closed(jira, keywords: list[str], exclude_key: str) -> list[Candidate]:
    """Fetch closed SCD tickets matching keywords via JQL text search."""
    # Use top 3 keywords to avoid overly narrow JQL
    terms = keywords[:3]
    text_clause = " OR ".join(f'text ~ "{t}"' for t in terms)
    jql = (
        f"project = SCD AND statusCategory = Done AND ({text_clause}) "
        f"ORDER BY updated DESC"
    )
    if exclude_key:
        jql += f" AND key != {exclude_key}"

    fields = ["summary", "description", "resolution", "comment",
              "customfield_10170", "customfield_10201"]
    issues = jira.search(jql, fields=fields, max_results=30)

    candidates = []
    for issue in issues:
        f = issue.get("fields", {})
        summary = f.get("summary", "")
        resolution = (f.get("resolution") or {}).get("name", "")
        topic = (f.get("customfield_10170") or {}).get("value", "")
        root_cause = (f.get("customfield_10201") or {}).get("value", "")

        desc_raw = f.get("description") or {}
        desc_text = (_extract_adf_text(desc_raw) if isinstance(desc_raw, dict) else str(desc_raw))[:600]

        # Include last comment as resolution context
        comments = (f.get("comment") or {}).get("comments", [])
        last_comment = ""
        if comments:
            last_body = comments[-1].get("body", {})
            last_comment = (_extract_adf_text(last_body) if isinstance(last_body, dict) else str(last_body))[:300]

        body = f"Description: {desc_text}\nResolution: {resolution}\nLast comment: {last_comment}"

        candidates.append(Candidate(
            source="jira_closed",
            ref_id=issue["key"],
            title=summary,
            body=body,
            url=f"https://servicecentral.atlassian.net/browse/{issue['key']}",
            metadata={"topic": topic, "root_cause": root_cause, "resolution": resolution},
        ))

    return candidates


def _fetch_kb_articles(jira, keywords: list[str]) -> list[Candidate]:
    """Fetch JSM Knowledge Base articles via Confluence REST API."""
    query = " ".join(keywords[:4])

    # CQL search for articles
    cql = urllib.parse.urlencode({"cql": f"type=article AND text~{query}", "limit": 20})
    path = f"/wiki/rest/api/content/search?{cql}&expand=body.view"

    try:
        data = jira._get(path)
    except Exception:
        # Fallback: title-based search
        title_encoded = urllib.parse.urlencode({"title": query, "limit": 20})
        data = jira._get(f"/wiki/rest/api/content/search?{title_encoded}&expand=body.view")

    results = data.get("results", [])
    candidates = []

    for article in results:
        title = article.get("title", "")
        article_id = str(article.get("id", ""))
        body_html = (article.get("body") or {}).get("view", {}).get("value", "")
        body_text = re.sub(r"<[^>]+>", " ", body_html)
        body_text = re.sub(r"\s+", " ", body_text).strip()[:800]

        candidates.append(Candidate(
            source="kb_article",
            ref_id=article_id,
            title=title,
            body=body_text,
            url=f"https://servicecentral.atlassian.net/wiki/pages/{article_id}",
        ))

    return candidates
