"""
Product lexicon matcher built from the cleaned report file.

The goal is to improve recognition of noisy user product queries by using
the product naming patterns already present in the catalog report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
import re
import threading
import unicodedata
from typing import Dict, List, Set, Tuple

from config.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class _LexiconEntry:
    raw: str
    norm: str
    tokens: Set[str]


_LEXICON_ENTRIES: List[_LexiconEntry] = []
_TOKEN_INDEX: Dict[str, Set[int]] = {}
_LEXICON_READY = False
_LEXICON_LOCK = threading.Lock()

_STOPWORDS = {
    "de",
    "da",
    "do",
    "das",
    "dos",
    "com",
    "sem",
    "para",
    "por",
    "e",
    "a",
    "o",
    "as",
    "os",
    "um",
    "uma",
    "uns",
    "umas",
}

_ABBREV_REPLACEMENTS: List[Tuple[str, str]] = [
    (r"\bc\/\b", " com "),
    (r"\bp\/\b", " para "),
    (r"\bpct\b", " pacote "),
    (r"\bund\b", " unidade "),
    (r"\bunid\b", " unidade "),
    (r"\bkg\s+kg\b", " kg "),
    (r"\bml\s+ml\b", " ml "),
    (r"\bg\s+g\b", " g "),
]


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_text(text: str) -> str:
    t = _strip_accents((text or "").lower())
    t = t.replace("*", " ")
    t = t.replace("/", " / ")
    t = re.sub(r"[^a-z0-9\s/\-]", " ", t)
    for pattern, repl in _ABBREV_REPLACEMENTS:
        t = re.sub(pattern, repl, t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tokens(text: str) -> Set[str]:
    out = set()
    for tk in _normalize_text(text).split():
        if not tk or tk in _STOPWORDS:
            continue
        if len(tk) == 1 and not tk.isdigit():
            continue
        out.add(tk)
    return out


def _lexicon_file_path() -> Path:
    return Path(__file__).resolve().parent.parent / "memory" / "product_lexicon_from_report.txt"


def _ensure_lexicon_loaded() -> None:
    global _LEXICON_READY
    if _LEXICON_READY:
        return
    with _LEXICON_LOCK:
        if _LEXICON_READY:
            return

        path = _lexicon_file_path()
        if not path.exists():
            logger.warning(f"Product lexicon file not found: {path}")
            _LEXICON_READY = True
            return

        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        entries: List[_LexiconEntry] = []
        token_index: Dict[str, Set[int]] = {}

        for raw in lines:
            name = (raw or "").strip()
            if not name:
                continue
            norm = _normalize_text(name)
            if not norm:
                continue
            toks = _tokens(norm)
            idx = len(entries)
            entries.append(_LexiconEntry(raw=name, norm=norm, tokens=toks))
            for tk in toks:
                token_index.setdefault(tk, set()).add(idx)

        _LEXICON_ENTRIES[:] = entries
        _TOKEN_INDEX.clear()
        _TOKEN_INDEX.update(token_index)
        _LEXICON_READY = True
        logger.info(f"Product lexicon loaded: {len(_LEXICON_ENTRIES)} entries")


def _score_query_against_entry(query_norm: str, query_tokens: Set[str], entry: _LexiconEntry) -> float:
    if not query_tokens or not entry.tokens:
        return 0.0

    overlap_tokens = len(query_tokens & entry.tokens)
    overlap_ratio = overlap_tokens / max(len(query_tokens), 1)
    precision_ratio = overlap_tokens / max(len(entry.tokens), 1)
    seq_ratio = difflib.SequenceMatcher(None, query_norm, entry.norm).ratio()

    contains_bonus = 0.0
    if query_norm and (query_norm in entry.norm or entry.norm in query_norm):
        contains_bonus += 0.10

    prefix_bonus = 0.0
    for tk in query_tokens:
        if any(et.startswith(tk) for et in entry.tokens):
            prefix_bonus += 0.02
    prefix_bonus = min(prefix_bonus, 0.10)

    score = (0.45 * overlap_ratio) + (0.25 * seq_ratio) + (0.20 * precision_ratio) + contains_bonus + prefix_bonus
    return round(min(score, 1.2), 4)


def suggest_queries_from_lexicon(query: str, limit: int = 3, min_score: float = 0.70) -> List[Tuple[str, float]]:
    """
    Suggest canonical product names from the report-based lexicon.
    Returns a list of (product_name, score).
    """
    _ensure_lexicon_loaded()
    if not _LEXICON_ENTRIES:
        return []

    q = (query or "").strip()
    if not q:
        return []

    q_norm = _normalize_text(q)
    q_tokens = _tokens(q_norm)
    if not q_tokens:
        return []

    # Candidate retrieval with token index (rarer tokens first).
    postings: List[Set[int]] = []
    for tk in q_tokens:
        ids = _TOKEN_INDEX.get(tk)
        if ids:
            postings.append(ids)

    candidate_ids: Set[int] = set()
    if postings:
        postings = sorted(postings, key=len)
        candidate_ids = set(postings[0])
        for s in postings[1:3]:
            inter = candidate_ids & s
            if inter:
                candidate_ids = inter
            if len(candidate_ids) <= 500:
                break

        if len(candidate_ids) < 5:
            # Broaden using unions of rare postings if intersection got too strict.
            candidate_ids = set()
            for s in postings[:3]:
                candidate_ids |= s
    else:
        return []

    if not candidate_ids:
        return []

    scored: List[Tuple[str, float]] = []
    for idx in candidate_ids:
        entry = _LEXICON_ENTRIES[idx]
        score = _score_query_against_entry(q_norm, q_tokens, entry)
        if score >= min_score:
            scored.append((entry.raw, score))

    if not scored:
        return []

    scored.sort(key=lambda x: x[1], reverse=True)

    # Dedup by normalized form keeping highest score.
    dedup: Dict[str, Tuple[str, float]] = {}
    for name, score in scored:
        key = _normalize_text(name)
        prev = dedup.get(key)
        if prev is None or score > prev[1]:
            dedup[key] = (name, score)

    out = list(dedup.values())
    out.sort(key=lambda x: x[1], reverse=True)
    return out[: max(1, int(limit or 3))]

