from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.config import settings
from app.services.relation_memory_ingestion import normalize_metric_code


SUPPORTED_LITERATURE_EXTENSIONS = {".md", ".txt", ".pdf"}
DEFAULT_CHUNK_CHARS = 1400


@dataclass(frozen=True)
class BusinessLiteratureChunk:
    id: str
    source_path: str
    title: str
    text: str
    terms: frozenset[str]


class BusinessLiteratureRetriever:
    """Small local retriever for grounding spreadsheet relation classification."""

    def __init__(
        self,
        *,
        root_dir: str | Path | None = None,
        top_k: int | None = None,
        max_chars_per_snippet: int | None = None,
    ):
        self.root_dir = _resolve_literature_dir(root_dir or settings.BUSINESS_LITERATURE_DIR)
        self.top_k = int(top_k if top_k is not None else settings.BUSINESS_LITERATURE_TOP_K)
        self.max_chars_per_snippet = int(
            max_chars_per_snippet
            if max_chars_per_snippet is not None
            else settings.BUSINESS_LITERATURE_MAX_CHARS_PER_SNIPPET
        )
        self._cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._cache_chunks: list[BusinessLiteratureChunk] = []

    def retrieve_for_pairs(self, proposed_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.top_k <= 0 or not proposed_pairs:
            return []
        chunks = self._load_chunks()
        if not chunks:
            return []

        query_text = _query_text_from_pairs(proposed_pairs)
        query_terms = _tokenize(query_text)
        if not query_terms:
            return []

        scored: list[tuple[float, BusinessLiteratureChunk]] = []
        for chunk in chunks:
            score = _score_chunk(chunk=chunk, query_terms=query_terms, query_text=query_text)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].source_path, item[1].id))

        snippets: list[dict[str, Any]] = []
        for score, chunk in scored[: self.top_k]:
            snippets.append(
                {
                    "id": chunk.id,
                    "source": chunk.source_path,
                    "title": chunk.title,
                    "score": round(score, 4),
                    "text": _truncate_text(chunk.text, self.max_chars_per_snippet),
                }
            )
        return snippets

    def _load_chunks(self) -> list[BusinessLiteratureChunk]:
        signature = self._directory_signature()
        if signature == self._cache_signature:
            return self._cache_chunks
        self._cache_signature = signature
        self._cache_chunks = _load_literature_chunks(self.root_dir)
        return self._cache_chunks

    def _directory_signature(self) -> tuple[tuple[str, int, int], ...]:
        if not self.root_dir.exists() or not self.root_dir.is_dir():
            return ()
        rows: list[tuple[str, int, int]] = []
        for path in sorted(self.root_dir.rglob("*")):
            if not _is_supported_literature_file(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append(
                (str(path.relative_to(self.root_dir)), int(stat.st_mtime_ns), int(stat.st_size))
            )
        return tuple(rows)


def _resolve_literature_dir(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return settings.BASE_DIR / path


def _load_literature_chunks(root_dir: Path) -> list[BusinessLiteratureChunk]:
    if not root_dir.exists() or not root_dir.is_dir():
        return []
    chunks: list[BusinessLiteratureChunk] = []
    for path in sorted(root_dir.rglob("*")):
        if not _is_supported_literature_file(path):
            continue
        text = _read_literature_text(path)
        if not text.strip():
            continue
        relative_source = str(path.relative_to(root_dir))
        title = path.stem.replace("_", " ").strip() or relative_source
        for chunk_index, chunk_text in enumerate(_split_text_into_chunks(text), start=1):
            terms = _tokenize(chunk_text)
            if not terms:
                continue
            chunks.append(
                BusinessLiteratureChunk(
                    id=f"{normalize_metric_code(relative_source)}:{chunk_index}",
                    source_path=relative_source,
                    title=title,
                    text=chunk_text,
                    terms=frozenset(terms),
                )
            )
    return chunks


def _is_supported_literature_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in SUPPORTED_LITERATURE_EXTENSIONS:
        return False
    return path.name.lower() not in {"readme.md", "readme.txt"}


def _read_literature_text(path: Path) -> str:
    try:
        if path.suffix.lower() == ".pdf":
            reader = PdfReader(str(path))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _split_text_into_chunks(text: str) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > DEFAULT_CHUNK_CHARS:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_text(paragraph, DEFAULT_CHUNK_CHARS))
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= DEFAULT_CHUNK_CHARS:
            current = candidate
            continue
        if current:
            chunks.append(current.strip())
        current = paragraph
    if current:
        chunks.append(current.strip())
    return chunks


def _split_long_text(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end].strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def _query_text_from_pairs(proposed_pairs: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for pair in proposed_pairs:
        parts.extend([str(pair.get("proposal_reason") or ""), str(pair.get("evidence") or "")])
        for metric_key in ("left_metric", "right_metric"):
            metric = pair.get(metric_key, {})
            parts.extend(
                [
                    str(metric.get("code") or ""),
                    str(metric.get("label") or ""),
                    " ".join(str(item) for item in metric.get("cell_refs", []) or []),
                ]
            )
    return " ".join(parts)


def _score_chunk(
    *, chunk: BusinessLiteratureChunk, query_terms: set[str], query_text: str
) -> float:
    overlap = query_terms & chunk.terms
    if not overlap:
        return 0.0
    score = len(overlap) / math.sqrt(max(len(chunk.terms), 1))
    normalized_chunk_text = chunk.text.lower()
    for phrase in _important_phrases(query_text):
        if phrase in normalized_chunk_text:
            score += 0.75
    return score


def _important_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for phrase in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9%/ ._-]{5,80}", text):
        normalized = " ".join(phrase.lower().split())
        if len(normalized) >= 6:
            phrases.append(normalized)
    return phrases[:20]


def _tokenize(text: str) -> set[str]:
    terms = {
        token.lower()
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", text)
        if token and token.lower() not in _STOP_WORDS
    }
    expanded = set(terms)
    for term in terms:
        normalized = normalize_metric_code(term)
        if normalized and normalized not in _STOP_WORDS:
            expanded.add(normalized)
    return expanded


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


_STOP_WORDS = {
    "and",
    "are",
    "for",
    "from",
    "has",
    "have",
    "into",
    "not",
    "that",
    "the",
    "this",
    "with",
    "или",
    "как",
    "для",
    "при",
    "что",
    "это",
}
