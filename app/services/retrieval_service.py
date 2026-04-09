from __future__ import annotations

from pathlib import Path
from typing import List

from app.schemas import RetrievedDocument


KB_DIR = Path("knowledge_base")


def _score_document(query: str, content: str) -> int:
    query_terms = [term.strip().lower() for term in query.split() if term.strip()]
    content_lower = content.lower()
    return sum(1 for term in query_terms if term in content_lower)


def retrieve_relevant_documents(
    *,
    query: str,
    max_documents: int = 3,
) -> List[RetrievedDocument]:
    """
    Very small local keyword-based retrieval for tutorial v1.
    Reads .md files from knowledge_base/ and returns top matches.
    """
    if not KB_DIR.exists() or not KB_DIR.is_dir():
        return []

    candidates: list[tuple[int, RetrievedDocument]] = []

    for file_path in KB_DIR.glob("*.md"):
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            continue

        score = _score_document(query, content)
        if score > 0:
            candidates.append(
                (
                    score,
                    RetrievedDocument(
                        source=file_path.name,
                        content=content[:2000],
                    ),
                )
            )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [doc for _, doc in candidates[:max_documents]]