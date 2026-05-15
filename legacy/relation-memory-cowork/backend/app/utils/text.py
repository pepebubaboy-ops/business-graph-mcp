from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "как",
    "по",
    "в",
    "на",
    "и",
    "или",
    "что",
    "это",
    "за",
}


def extract_keywords(text: str) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for token in TOKEN_RE.findall(text.lower()):
        if len(token) < 2 or token in STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
    return keywords
