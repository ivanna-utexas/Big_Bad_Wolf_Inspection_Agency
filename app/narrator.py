"""The wolf's voice. Narrates a verdict the scorer already decided.

Uses Gemini when GEMINI_API_KEY is set: strict JSON, validated, one retry,
then canned lines. Without a key it goes straight to canned lines.
"""

import hashlib
import json
import logging
from typing import Any

import httpx

log = logging.getLogger("bbw.narrator")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_LINE = 280

SYSTEM_PROMPT = """You are the Big Bad Wolf, now working as a (satirical) building inspector for apartments near UT Austin.
Write short, dramatic, pun-friendly lines in character. Playful, never cruel.

Hard rules:
- The verdict and huff_risk are already decided. Do not change or contradict them.
- Only talk about what "reviewers say". Never state facts about the building as true.
- Do not invent facts, numbers, dates, or reviewer details. Only use numbers given to you.
- Never name or describe individual reviewers, staff, or any specific person.
- Only reference snippet IDs from the input. Copy the theme names exactly as given.
- Brick = solid, sticks = shaky, straw = blows right over.

Reply with JSON only, exactly this shape:
{"wolf_opening": "1 sentence", "verdict_line": "1-2 sentences", "themes": [{"theme": "...", "wolf_comment": "1 sentence", "snippet_ids": ["..."]}], "closing_threat": "1 sentence, playful"}"""

CANNED = {
    "brick": {
        "openings": [
            "I huffed. I puffed. I pulled a hamstring.",
            "Another brick house? Grandma, what big walls you have.",
            "Little pigs, little pigs, your reviewers seem rather pleased.",
        ],
        "verdicts": [
            "Brick. Reviewers mostly say this place holds together, and my lungs agree.",
            "Brick. By the sound of the reviews, this one would survive me and a thunderstorm.",
        ],
        "closings": [
            "I'll be back next semester with a bigger breath.",
            "Fine. I'm going to go sulk in the forest.",
        ],
    },
    "sticks": {
        "openings": [
            "Hmm. Sturdy-ish. Wobbly-ish. Let me take a deep breath.",
            "I've seen worse. I've also seen much, much better.",
            "Reviewers paint a picture held together with twigs and hope.",
        ],
        "verdicts": [
            "Sticks. Reviewers say it mostly stands, but one good huff might rattle a few things loose.",
            "Sticks. Not a total pushover according to reviewers, but I like my chances.",
        ],
        "closings": [
            "Keep your lease handy and your renter's insurance handier.",
            "I'll huff gently. For now.",
        ],
    },
    "straw": {
        "openings": [
            "Oh, this is going to be fun.",
            "I barely inhaled and the reviews were already falling over.",
            "Little pigs, little pigs, reviewers have some things to tell me.",
        ],
        "verdicts": [
            "Straw. If the reviewers are right, this one blows right over.",
            "Straw. Going by what reviewers say, I won't even need my second puff.",
        ],
        "closings": [
            "Huff, puff, and read your lease twice.",
            "I'd bring a flashlight and a dehumidifier. Just saying.",
        ],
    },
    "not_enough_data": {
        "openings": ["I sniffed around, but the trail is cold."],
        "verdicts": [
            "Not enough reviews to judge. Even a wolf needs more than a few crumbs to follow.",
        ],
        "closings": ["Come back when more pigs have written in."],
    },
}

CANNED_THEME = {
    "Water & Mold": "Reviewers mention water where water shouldn't be. My breath isn't the only thing that's damp.",
    "Pests": "Reviewers report uninvited roommates. Even I knock first.",
    "Broken Basics": "Reviewers say the basics take breaks. So does my patience.",
    "Safety": "Reviewers have safety worries, and I'm the one who's supposed to be scary.",
    "Maintenance": "Reviewers say repair requests travel slower than a pig on vacation.",
    "Money & Deposits": "Reviewers grumble about fees and deposits. Hold on to your piggy bank.",
    "Office Attitude": "Reviewers have opinions about the front office. Strong ones.",
    "Noise": "Reviewers say the walls let the party in. I could huff through those.",
    "Parking & Packages": "Reviewers report towing and vanishing packages. Things go missing around here, and it wasn't me.",
    "Solid Upkeep": "Reviewers say things get fixed. Annoyingly sturdy.",
    "Peace & Quiet": "Reviewers say it's quiet. Too quiet. Good for studying, bad for wolves.",
    "Feels Safe": "Reviewers say they feel safe here. Rude, honestly.",
}


def _pick(options: list[str], seed: str) -> str:
    idx = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(options)
    return options[idx]


def canned(context: dict) -> dict:
    verdict = context.get("verdict", "sticks")
    lines = CANNED.get(verdict, CANNED["sticks"])
    seed = context.get("title", "") + verdict
    return {
        "wolf_opening": _pick(lines["openings"], seed + "o"),
        "verdict_line": _pick(lines["verdicts"], seed + "v"),
        "themes": [
            {
                "theme": t["theme"],
                "wolf_comment": CANNED_THEME.get(t["theme"], "Reviewers had a few things to say about this one."),
                "snippet_ids": list(t.get("snippet_ids", [])),
            }
            for t in context.get("themes", [])
        ],
        "closing_threat": _pick(lines["closings"], seed + "c"),
        "source": "canned",
    }


def _clean_line(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    if not value:
        return None
    return value[:MAX_LINE]


def validate(raw: Any, context: dict) -> dict | None:
    """Return a cleaned narration dict, or None if the model output is unusable."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key in ("wolf_opening", "verdict_line", "closing_threat"):
        line = _clean_line(raw.get(key))
        if line is None:
            return None
        out[key] = line

    allowed = {t["theme"]: set(t.get("snippet_ids", [])) for t in context.get("themes", [])}
    themes_raw = raw.get("themes")
    if not isinstance(themes_raw, list):
        return None
    by_theme: dict[str, dict] = {}
    for item in themes_raw:
        if not isinstance(item, dict):
            continue
        name = item.get("theme")
        comment = _clean_line(item.get("wolf_comment"))
        if name not in allowed or comment is None or name in by_theme:
            continue
        ids = item.get("snippet_ids") if isinstance(item.get("snippet_ids"), list) else []
        by_theme[name] = {
            "theme": name,
            "wolf_comment": comment,
            # Drop any snippet id the model made up.
            "snippet_ids": [i for i in ids if i in allowed[name]],
        }
    # Every theme we sent must come back with a comment.
    if set(by_theme) != set(allowed):
        return None
    out["themes"] = [by_theme[t["theme"]] for t in context.get("themes", [])]
    return out


class Narrator:
    def __init__(self, api_key: str = "", model: str = "gemini-2.5-flash", http: httpx.Client | None = None):
        self.api_key = api_key
        self.model = model
        self.http = http or httpx.Client(timeout=20)

    def narrate(self, context: dict) -> dict:
        if not self.api_key or context.get("verdict") == "not_enough_data":
            return canned(context)
        for attempt in range(2):
            try:
                raw = self._call(context)
            except Exception as exc:  # network, HTTP, or JSON trouble: retry once, then canned
                log.warning("Narrator attempt %d failed: %s", attempt + 1, type(exc).__name__)
                continue
            result = validate(raw, context)
            if result:
                result["source"] = "gemini"
                return result
            log.warning("Narrator attempt %d returned invalid JSON shape", attempt + 1)
        return canned(context)

    def _call(self, context: dict) -> Any:
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(context)}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
        }
        resp = self.http.post(
            GEMINI_URL.format(model=self.model),
            headers={"x-goog-api-key": self.api_key},
            json=body,
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


def build_context(title: str, scored: dict) -> dict:
    """The only things the model is allowed to see. Snippet text is included so
    comments can be on-topic; reviewer names were never stored."""
    return {
        "title": title,
        "verdict": scored["verdict"],
        "huff_risk": scored["huff_risk"],
        "review_count": scored["sample"]["reviews_used"],
        "date_range": [scored["sample"]["oldest"], scored["sample"]["newest"]],
        "themes": [
            {
                "theme": t["theme"],
                "tone": t["tone"],
                "snippet_ids": [s["id"] for s in t["snippets"]],
                "snippets": [{"id": s["id"], "text": s["text"]} for s in t["snippets"]],
            }
            for t in scored["themes"]
        ],
    }
