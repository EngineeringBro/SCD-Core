"""
CX Reranker — Step 2 of the General Module pipeline.

Scores Candidate objects against the incoming ticket using BM25.
Pure stdlib — no external dependencies required.
Returns the top_k highest-scoring candidates.
"""
from __future__ import annotations
import math
import os
import re
from dataclasses import dataclass
from modules.general_module_v0_2.core_cx_retriever import Candidate

# BM25 hyperparameters
K1 = 1.5    # term frequency saturation
B = 0.75    # length normalization


@dataclass
class ScoredCandidate:
    candidate: Candidate
    bm25_score: float      # raw BM25 score
    relative_score: float  # fraction of best score (0.0 – 1.0)

_STOP_WORDS = {
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for",
    "of", "and", "or", "but", "not", "with", "this", "that", "was",
    "are", "be", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "i", "we",
    "you", "he", "she", "they", "my", "our", "your", "their", "its",
}


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z]{3,}", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS]


def _query_tokens(ticket: dict) -> list[str]:
    fields = ticket.get("fields", {})
    summary = fields.get("summary", "")
    topic = (fields.get("customfield_10170") or {}).get("value", "")
    return _tokenize(f"{summary} {topic}")


def rerank(candidates: list[Candidate], ticket: dict, top_k: int = 5) -> list[ScoredCandidate]:
    """
    BM25-score all candidates against the ticket, return top_k as ScoredCandidate.
    Falls back to wrapping candidates[:top_k] with score=0 if corpus is too small.
    """
    if not candidates:
        return []

    query_terms = _query_tokens(ticket)
    if not query_terms:
        return [ScoredCandidate(c, 0.0, 0.0) for c in candidates[:top_k]]

    # Tokenize all documents
    doc_tokens: list[list[str]] = [_tokenize(f"{c.title} {c.body}") for c in candidates]
    N = len(doc_tokens)
    avgdl = sum(len(d) for d in doc_tokens) / N if N > 0 else 1

    # IDF for each query term
    idf: dict[str, float] = {}
    for term in set(query_terms):
        df = sum(1 for d in doc_tokens if term in d)
        idf[term] = math.log((N - df + 0.5) / (df + 0.5) + 1)

    # Score each candidate
    scored: list[tuple[float, Candidate]] = []
    for doc, candidate in zip(doc_tokens, candidates):
        doc_len = len(doc)
        tf_map: dict[str, int] = {}
        for t in doc:
            tf_map[t] = tf_map.get(t, 0) + 1

        score = 0.0
        for term in query_terms:
            tf = tf_map.get(term, 0)
            if tf == 0:
                continue
            numerator = tf * (K1 + 1)
            denominator = tf + K1 * (1 - B + B * doc_len / avgdl)
            score += idf.get(term, 0.0) * (numerator / denominator)

        # Slight boost for closed Jira tickets — more actionable than KB articles
        if candidate.source == "jira_closed":
            score *= 1.1

        scored.append((score, candidate))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return []

    best_score = scored[0][0]

    # Relative threshold: keep candidates scoring at least MIN_CANDIDATE_RELEVANCE
    # fraction of the top score (default 0.3 = 30%). Configurable via env var.
    min_relevance = float(os.environ.get("MIN_CANDIDATE_RELEVANCE", "0.3"))

    top = []
    for score, candidate in scored[:top_k]:
        relative = score / best_score if best_score > 0 else 0.0
        if relative >= min_relevance:
            top.append(ScoredCandidate(candidate, score, relative))

    print(
        f"[cx_reranker] {len(top)}/{len(scored)} candidates passed threshold "
        f"(min_relevance={min_relevance}, "
        f"scores: {[round(s.bm25_score, 2) for s in top]})"
    )
    return top
