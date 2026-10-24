"""Deterministic huff-risk scorer.

Pure functions only: the same reviews, signals and `now` always give the same
verdict. The LLM never touches anything in this file.
"""

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

# ---- tunable constants ---------------------------------------------------

MIN_REVIEWS = 15  # fewer usable reviews than this -> "not_enough_data"
BRICK_BELOW = 35  # huff_risk < 35  -> brick
STRAW_ABOVE = 65  # huff_risk > 65  -> straw (35..65 is sticks)

BASE_RISK = 40.0  # where a building with no signals and a 3.5 star average lands
SIGNAL_SCALE = 10.0  # multiplier on the average signal weight per review
STAR_PIVOT = 3.5  # star average that adds nothing
STAR_SCALE = 12.0  # risk points per star below/above the pivot
TOPIC_SCALE = 15.0
TOPIC_CAP = 8.0
COPY_PASTE_PENALTY = 5.0
VARIED_REPLY_BONUS = 3.0
COPY_PASTE_SIMILARITY = 0.6
VARIED_SIMILARITY = 0.35
MIN_REPLIES_FOR_SIGNAL = 3

# Reviews from the lowest-rated sort and keyword probes over-sample complaints,
# so they count for less than the newest-first sample.
SOURCE_FACTOR = {"newest": 1.0, "low": 0.6, "probe": 0.6}

SNIPPET_CHARS = 200
SNIPPETS_PER_THEME = 2
TOP_THEMES = 3

NEGATION = re.compile(
    r"(?:\bno|\bnot|\bzero|\bwithout|\bnever (?:had|seen|saw|noticed)(?: any| a)?|\bnot a single|\bany|\bdon'?t|\bdidn'?t)\s+$",
    re.I,
)


@dataclass(frozen=True)
class Signal:
    id: str
    label: str
    theme: str
    weight: float
    negatable: bool
    patterns: tuple[re.Pattern, ...]


def load_signals(path: Path | str) -> tuple[list[Signal], list[str]]:
    data = json.loads(Path(path).read_text())
    signals = [
        Signal(
            id=s["id"],
            label=s.get("label", s["id"]),
            theme=s["theme"],
            weight=float(s["weight"]),
            negatable=bool(s.get("negatable", False)),
            patterns=tuple(re.compile(t, re.I) for t in s["terms"]),
        )
        for s in data["signals"]
    ]
    return signals, list(data.get("probes", []))


# ---- review normalization ------------------------------------------------


def snippet_id(raw_id: str) -> str:
    return "r" + hashlib.sha1(raw_id.encode()).hexdigest()[:8]


def normalize_review(raw: dict, source: str) -> dict | None:
    """Keep only what we score on. Reviewer name, link and photos are dropped here."""
    extracted = raw.get("extracted_snippet") or {}
    text = extracted.get("translated") or extracted.get("original") or raw.get("snippet") or ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    if not text:
        return None
    response = raw.get("response") or {}
    resp_extracted = response.get("extracted_snippet") or {}
    response_text = resp_extracted.get("original") or response.get("snippet") or None
    raw_id = str(raw.get("review_id") or text)
    try:
        rating = float(raw["rating"]) if raw.get("rating") is not None else None
    except (TypeError, ValueError):
        rating = None
    try:
        likes = int(raw.get("likes") or 0)
    except (TypeError, ValueError):
        likes = 0
    return {
        "id": snippet_id(raw_id),
        "text": text,
        "rating": rating,
        "date": raw.get("iso_date"),
        "likes": max(likes, 0),
        "response": response_text,
        "source": source,
    }


def merge_reviews(groups: list[tuple[str, list[dict]]]) -> list[dict]:
    """Normalize and de-duplicate. Earlier groups win, so pass 'newest' first."""
    seen: set[str] = set()
    out: list[dict] = []
    for source, raws in groups:
        for raw in raws or []:
            review = normalize_review(raw, source)
            if review and review["id"] not in seen:
                seen.add(review["id"])
                out.append(review)
    return out


# ---- weights -------------------------------------------------------------


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _iso(value: str | None) -> str | None:
    d = parse_date(value)
    return d.isoformat() if d else None


def recency_weight(review_date: date | None, today: date) -> float:
    """1.0 for the last ~3 months, sliding down to 0.3 at ~3 years and older."""
    if review_date is None:
        return 0.5
    age = (today - review_date).days
    if age <= 90:
        return 1.0
    if age >= 1095:
        return 0.3
    return 1.0 - 0.7 * (age - 90) / (1095 - 90)


def engagement_weight(likes: int) -> float:
    return 1 + min(max(likes, 0), 10) * 0.05


def review_weight(review: dict, today: date) -> float:
    return (
        recency_weight(parse_date(review.get("date")), today)
        * engagement_weight(review.get("likes", 0))
        * SOURCE_FACTOR.get(review.get("source", "newest"), 1.0)
    )


# ---- matching ------------------------------------------------------------


def find_match(signal: Signal, text: str) -> re.Match | None:
    for pattern in signal.patterns:
        for m in pattern.finditer(text):
            if signal.negatable and NEGATION.search(text[max(0, m.start() - 25) : m.start()]):
                continue
            return m
    return None


# ---- privacy -------------------------------------------------------------

_NAME_CONTEXT = re.compile(
    r"\b((?i:manager|agent|leasing|staff|employee|office|maintenance|tech|guy|man|woman|lady|named|thanks to|thank you|shout ?out to|ask for)"
    r"(?:\s+(?i:named|called|is|was))?)\s+((?:Mr|Ms|Mrs|Miss)\.?\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"
)
_HONORIFIC = re.compile(r"\b(?:Mr|Ms|Mrs|Miss)\.?\s+[A-Z][a-z]+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_HANDLE = re.compile(r"@\w+")


def redact(text: str) -> str:
    """Best-effort scrub of people's names and contact details from a snippet."""
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    text = _NAME_CONTEXT.sub(lambda m: f"{m.group(1)} [name]", text)
    text = _HONORIFIC.sub("[name]", text)
    text = _HANDLE.sub("[name]", text)
    return text


def excerpt(text: str, start: int, limit: int = SNIPPET_CHARS) -> str:
    """A ~limit-char window around position `start`, cut on word boundaries."""
    if len(text) <= limit:
        return redact(text)
    lo = max(0, min(start - limit // 3, len(text) - limit))
    hi = lo + limit
    if lo > 0:
        space = text.find(" ", lo)
        lo = space + 1 if 0 <= space < lo + 20 else lo
    if hi < len(text):
        space = text.rfind(" ", lo, hi)
        hi = space if space > lo + limit // 2 else hi
    piece = text[lo:hi].strip()
    return redact(("…" if lo > 0 else "") + piece + ("…" if hi < len(text) else ""))


# ---- owner replies -------------------------------------------------------


def _reply_words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def reply_similarity(replies: list[str]) -> float | None:
    """Mean pairwise similarity of owner replies (1.0 = identical copy-paste)."""
    replies = [r for r in replies if r][:10]
    if len(replies) < MIN_REPLIES_FOR_SIGNAL:
        return None
    words = [_reply_words(r) for r in replies]
    scores = [difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() for a, b in combinations(words, 2)]
    return sum(scores) / len(scores)


# ---- main entry point ----------------------------------------------------


def verdict_for(huff_risk: float) -> str:
    if huff_risk < BRICK_BELOW:
        return "brick"
    if huff_risk > STRAW_ABOVE:
        return "straw"
    return "sticks"


def score(
    reviews: list[dict],
    signals: list[Signal],
    today: date,
    topics: list[dict] | None = None,
    place_review_count: int | None = None,
) -> dict[str, Any]:
    usable = [r for r in reviews if r.get("text")]
    dates = sorted(d.isoformat() for d in (parse_date(r.get("date")) for r in usable) if d)
    sample = {
        "reviews_used": len(usable),
        "oldest": dates[0] if dates else None,
        "newest": dates[-1] if dates else None,
    }
    if len(usable) < MIN_REVIEWS:
        return {
            "verdict": "not_enough_data",
            "huff_risk": None,
            "sample": sample,
            "themes": [],
            "signals_fired": [],
            "components": {},
        }

    # 1. Signals per review.
    total_weight = 0.0
    total_contrib = 0.0
    fired: dict[str, dict] = {}
    theme_hits: dict[str, list[dict]] = {}
    for review in usable:
        w = review_weight(review, today)
        total_weight += w
        for signal in signals:
            m = find_match(signal, review["text"])
            if not m:
                continue
            contrib = signal.weight * w
            total_contrib += contrib
            entry = fired.setdefault(
                signal.id,
                {"term": signal.label, "theme": signal.theme, "count": 0, "weight": signal.weight, "impact": 0.0},
            )
            entry["count"] += 1
            entry["impact"] += contrib
            theme_hits.setdefault(signal.theme, []).append(
                {"review": review, "contrib": contrib, "pos": m.start()}
            )

    signal_component = SIGNAL_SCALE * total_contrib / total_weight if total_weight else 0.0

    # 2. Recent star ratings (newest-first sample only, recency-weighted).
    rated = [r for r in usable if r.get("rating") is not None and r.get("source") == "newest"]
    if not rated:
        rated = [r for r in usable if r.get("rating") is not None]
    star_component = 0.0
    star_avg = None
    if rated:
        ws = [recency_weight(parse_date(r.get("date")), today) for r in rated]
        star_avg = sum(r["rating"] * w for r, w in zip(rated, ws)) / sum(ws)
        star_component = (STAR_PIVOT - star_avg) * STAR_SCALE

    # 3. Google's topic chips (e.g. "roaches 14") from the first reviews page.
    topic_component = 0.0
    if topics:
        topic_sum = 0.0
        for topic in topics:
            keyword = str(topic.get("keyword") or "")
            mentions = int(topic.get("mentions") or 0)
            for signal in signals:
                if keyword and any(p.search(keyword) for p in signal.patterns):
                    topic_sum += signal.weight * mentions
                    break
        denom = max(place_review_count or 0, len(usable), 1)
        topic_component = max(-TOPIC_CAP, min(TOPIC_CAP, TOPIC_SCALE * topic_sum / denom))

    # 4. Owner replies to negative reviews: copy-paste vs. specific.
    replies = [r["response"] for r in usable if r.get("response") and (r.get("rating") or 5) <= 3]
    similarity = reply_similarity(replies)
    mgmt_component = 0.0
    if similarity is not None:
        if similarity >= COPY_PASTE_SIMILARITY:
            mgmt_component = COPY_PASTE_PENALTY
            fired["_copy_paste"] = {
                "term": "copy-paste owner replies", "theme": "Office Attitude",
                "count": len(replies), "weight": COPY_PASTE_PENALTY, "impact": COPY_PASTE_PENALTY,
            }
        elif similarity <= VARIED_SIMILARITY:
            mgmt_component = -VARIED_REPLY_BONUS
            fired["_varied"] = {
                "term": "specific owner replies", "theme": "Office Attitude",
                "count": len(replies), "weight": -VARIED_REPLY_BONUS, "impact": -VARIED_REPLY_BONUS,
            }

    raw = BASE_RISK + signal_component + star_component + topic_component + mgmt_component
    huff_risk = int(round(max(0.0, min(100.0, raw))))

    # 5. Themes: rank by total weight (absolute, so reassuring themes can show up too).
    ranked = sorted(
        theme_hits.items(),
        key=lambda kv: (-abs(sum(h["contrib"] for h in kv[1])), kv[0]),
    )[:TOP_THEMES]
    used: set[str] = set()
    themes = []
    for theme, hits in ranked:
        total = sum(h["contrib"] for h in hits)
        ordered = sorted(
            hits,
            key=lambda h: (-abs(h["contrib"]), -(h["review"].get("likes") or 0), h["review"]["id"]),
        )
        picks: list[dict] = []
        for allow_reuse in (False, True):
            for h in ordered:
                rid = h["review"]["id"]
                if len(picks) >= SNIPPETS_PER_THEME or any(p["review"]["id"] == rid for p in picks):
                    continue
                if not allow_reuse and rid in used:
                    continue
                picks.append(h)
        for p in picks:
            used.add(p["review"]["id"])
        themes.append(
            {
                "theme": theme,
                "tone": "good" if total < 0 else "bad",
                "weight": round(total, 2),
                "snippets": [
                    {
                        "id": p["review"]["id"],
                        "text": excerpt(p["review"]["text"], p["pos"]),
                        "rating": p["review"].get("rating"),
                        "date": _iso(p["review"].get("date")),
                    }
                    for p in picks
                ],
            }
        )

    signals_fired = sorted(
        ({**v, "impact": round(v["impact"], 2)} for v in fired.values()),
        key=lambda s: (-abs(s["impact"]), s["term"]),
    )

    return {
        "verdict": verdict_for(huff_risk),
        "huff_risk": huff_risk,
        "sample": sample,
        "themes": themes,
        "signals_fired": signals_fired,
        "components": {
            "base": BASE_RISK,
            "signals": round(signal_component, 2),
            "stars": round(star_component, 2),
            "star_average": round(star_avg, 2) if star_avg is not None else None,
            "topics": round(topic_component, 2),
            "owner_replies": round(mgmt_component, 2),
            "reply_similarity": round(similarity, 2) if similarity is not None else None,
        },
    }
