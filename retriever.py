"""Retrieval for the RAG step: finds the knowledge-base entries that match a question.

No outside libraries. Each entry has a `keywords` list. An entry scores 3 points for every
keyword found in the query, plus 1 point for every other word it shares with the entry's
text. Entries below `min_score` are dropped, so an off-topic query returns nothing.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge_base"
_WORD = re.compile(r"[a-z]+")
_STOPWORDS = {"a", "an", "the", "of", "to", "and", "or", "is", "in", "for", "with", "on", "at", "it"}


def _tokens(text: str) -> set[str]:
    """Lowercase words with a trailing plural 's' removed, so 'grapes' matches 'grape'."""
    words = {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _WORD.findall(text.lower())}
    return words - _STOPWORDS


class KnowledgeBase:
    def __init__(self, directory: Path = KNOWLEDGE_DIR) -> None:
        self.docs: list[dict] = []
        for path in sorted(Path(directory).glob("*.json")):
            try:
                self.docs.extend(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                # Guardrail: a broken data file must not crash the app, but we must say so.
                log.error("Could not load knowledge file %s: %s", path.name, exc)
        log.info("Knowledge base loaded: %d entries", len(self.docs))

    def retrieve(
        self,
        query: str,
        species: str | None = None,
        topic_prefix: str | None = None,
        k: int = 3,
        min_score: float = 3.0,
    ) -> list[dict]:
        """Return up to k entries, best first. Optionally limit to a species or topic."""
        query_tokens = _tokens(query)
        scored: list[tuple[float, dict]] = []
        for doc in self.docs:
            if species and species.lower() not in doc.get("species", [species.lower()]):
                continue
            if topic_prefix and not doc.get("topic", "").startswith(topic_prefix):
                continue
            keyword_hits = len(query_tokens & {t for kw in doc.get("keywords", []) for t in _tokens(kw)})
            overlap = len(query_tokens & _tokens(doc.get("text", "")))
            score = 3 * keyword_hits + overlap
            if score >= min_score:
                scored.append((score, doc))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        results = [doc for _, doc in scored[:k]]
        log.debug("retrieve(%r, species=%s, topic=%s) -> %s", query, species, topic_prefix, [d["id"] for d in results])
        return results
