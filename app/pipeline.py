"""search -> inspect -> (compare). Glue between SerpApi, the scorer and the narrator."""

import hashlib
import json
import logging
import re
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Callable

from . import scorer
from .cache import Cache
from .narrator import Narrator, build_context
from .serpapi_client import SerpApiClient, SerpApiError

log = logging.getLogger("bbw.pipeline")

NEWEST_PAGES = 2
MAX_COMPARE = 10  # "My List" can hold up to this many homes
DATA_ID_RE = re.compile(r"^0x[0-9a-fA-F]+:0x[0-9a-fA-F]+$")
MAX_QUERY_LEN = 100


class InputError(ValueError):
    pass


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _num(value, cast=float):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def google_maps_url(title: str, address: str, place_id: str = "") -> str:
    """Link to the place on Google Maps, where its reviews live."""
    params = {"api": "1", "query": " ".join(x for x in (title, address) if x)}
    if place_id:
        params["query_place_id"] = place_id  # pins the exact place instead of a text search
    return "https://www.google.com/maps/search/?" + urlencode(params)


# Rough box around the City of Austin (lat, lng).
AUSTIN_BOUNDS = ((30.10, -97.95), (30.52, -97.55))

# Google Maps place types that count as somewhere people live.
RESIDENTIAL_WORDS = (
    "apartment", "condominium", "condo", "housing", "residential", "dormitory", "townhouse",
    "townhome", "duplex", "loft", "mobile home park", "co-op", "cooperative",
)


def _in_austin(item: dict) -> bool:
    gps = item.get("gps_coordinates") or {}
    lat, lng = _num(gps.get("latitude")), _num(gps.get("longitude"))
    if lat is not None and lng is not None:
        (south, west), (north, east) = AUSTIN_BOUNDS
        return south <= lat <= north and west <= lng <= east
    return "austin" in (item.get("address") or "").lower()


def _place_types(item: dict) -> list[str]:
    """Google's place types. `type` is a string in local_results but a list in place_results (single exact match)."""
    out = []
    for value in (item.get("type"), item.get("types")):
        values = value if isinstance(value, list) else [value]
        out += [v for v in values if isinstance(v, str) and v]
    return out


# Dorms are often typed only as "Building" or "University department", so also trust the name.
# Plain "Hall" isn't enough: most UT academic buildings are halls too.
DORM_NAME_RE = re.compile(r"\bdorm(itory|itories|s)?\b|\bresiden(ce|tial|t) halls?\b|\bres halls?\b", re.IGNORECASE)


def _is_residential(item: dict) -> bool:
    text = " ".join(_place_types(item)).lower()
    if any(word in text for word in RESIDENTIAL_WORDS):
        return True
    return bool(DORM_NAME_RE.search(item.get("title") or ""))


# ---- rent ------------------------------------------------------------------
# Monthly rent comes from a regular Google search. Listing sites like Apartments.com
# give a structured "price_from / price_to"; otherwise we read $ amounts out of
# snippets that mention the building by name. Anything outside this band is not a
# monthly rent (fees, yearly dorm rates, deposits...).
RENT_MIN, RENT_MAX = 300, 6000
MONEY_RE = re.compile(r"\$\s?(\d{1,2},\d{3}|\d{3,4})(?:\.\d{2})?(?!\s*(?:off|in savings|savings|/\s*y|per year|a year|/yr))", re.I)


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return re.sub(r"^the ", "", text)


def parse_rent(data: dict, title: str) -> dict | None:
    """Rent range ({low, high, source}) for `title` from a google search payload, or None."""
    name = _norm(title)
    if not name:
        return None
    rich, loose = [], []
    for r in data.get("organic_results") or []:
        source = r.get("source") or r.get("displayed_link") or ""
        text = f"{source} {r.get('title') or ''} {r.get('snippet') or ''}"
        source = source.split(" · ")[0]  # "Facebook · The Castilian Residence Hall" -> "Facebook"
        if name not in _norm(text):
            continue  # generic "average rent in Austin" pages would skew the range
        ext = ((r.get("rich_snippet") or {}).get("top") or {}).get("detected_extensions") or {}
        lo, hi = _num(ext.get("price_from")), _num(ext.get("price_to"))
        if lo is not None and RENT_MIN <= lo <= RENT_MAX:
            hi = hi if hi is not None and lo <= hi <= RENT_MAX else lo
            rich.append((lo, hi, source))
            continue
        for m in MONEY_RE.finditer(r.get("snippet") or ""):
            value = float(m.group(1).replace(",", ""))
            if RENT_MIN <= value <= RENT_MAX:
                loose.append((value, value, source))
    found = rich or loose
    if not found:
        return None
    sources = list(dict.fromkeys(s for _, _, s in found if s))
    return {
        "low": int(min(lo for lo, _, _ in found)),
        "high": int(max(hi for _, hi, _ in found)),
        "source": sources[0] if sources else "",
    }


def _candidate(item: dict) -> dict | None:
    if not item.get("data_id"):
        return None
    if not (_in_austin(item) and _is_residential(item)):
        return None
    gps = item.get("gps_coordinates") or {}
    return {
        "lat": _num(gps.get("latitude")),
        "lng": _num(gps.get("longitude")),
        "title": item.get("title") or "Unnamed building",
        "address": item.get("address") or "",
        "data_id": item["data_id"],
        "place_id": item.get("place_id") or "",
        "rating": _num(item.get("rating")),
        "reviews": _num(item.get("reviews"), int),
        "thumbnail": item.get("thumbnail") or "",
        "type": (_place_types(item) or [""])[0],
    }


class Inspector:
    def __init__(
        self,
        client: SerpApiClient,
        cache: Cache,
        narrator: Narrator,
        signals: list[scorer.Signal],
        probes: list[str],
        today: Callable[[], date] = _today,
        autocomplete_enabled: bool = True,
    ):
        self.client = client
        self.cache = cache
        self.narrator = narrator
        self.signals = signals
        self.probes = probes
        self.today = today
        self.autocomplete_enabled = autocomplete_enabled
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.compare_pool = ThreadPoolExecutor(max_workers=3)

    # ---- search ------------------------------------------------------------

    def search(self, q: str) -> dict:
        q = " ".join((q or "").split())[:MAX_QUERY_LEN]
        if len(q) < 2:
            raise InputError("Type at least two letters, little pig.")
        query = q if "austin" in q.lower() else f"{q} Austin TX"
        data, _ = self.client.maps_search(query)
        items = data.get("local_results") or []
        if not items and isinstance(data.get("place_results"), dict):
            items = [data["place_results"]]
        candidates = [c for c in (_candidate(i) for i in items) if c][:3]
        for c in candidates:
            self.cache.set(f"place:{c['data_id']}", c)
        return {"candidates": candidates}

    def autocomplete(self, q: str) -> dict:
        q = " ".join((q or "").split())[:MAX_QUERY_LEN]
        if not self.autocomplete_enabled or len(q) < 3:
            return {"suggestions": []}
        data, _ = self.client.autocomplete(q)
        out = []
        for s in data.get("suggestions") or []:
            value = s.get("value")
            # Only suggest places in Austin; the subtext is the place's address or area.
            if value and "austin" in (s.get("subtext") or "").lower():
                out.append({"value": value, "subtext": s.get("subtext") or ""})
        return {"suggestions": out[:5]}

    # ---- inspect -----------------------------------------------------------

    def _fetch_reviews(self, data_id: str) -> tuple[dict, list[tuple[str, list[dict]]], bool]:
        first, first_cached = self.client.reviews(data_id, sort_by="newestFirst")
        all_cached = first_cached

        def newest_rest() -> tuple[list[dict], bool]:
            reviews, cached, page = [], True, first
            for _ in range(NEWEST_PAGES - 1):
                token = (page.get("serpapi_pagination") or {}).get("next_page_token")
                if not token:
                    break
                page, hit = self.client.reviews(data_id, sort_by="newestFirst", next_page_token=token)
                cached = cached and hit
                reviews += page.get("reviews") or []
            return reviews, cached

        def low() -> tuple[list[dict], bool]:
            page, hit = self.client.reviews(data_id, sort_by="ratingLow")
            return page.get("reviews") or [], hit

        def probe(keyword: str) -> tuple[list[dict], bool]:
            page, hit = self.client.reviews(data_id, sort_by="newestFirst", query=keyword)
            return page.get("reviews") or [], hit

        groups: list[tuple[str, list[dict]]] = [("newest", first.get("reviews") or [])]
        if first.get("empty") or not first.get("reviews"):
            return first, groups, all_cached

        jobs = [("newest", self.pool.submit(newest_rest)), ("low", self.pool.submit(low))]
        jobs += [("probe", self.pool.submit(probe, k)) for k in self.probes]
        for source, future in jobs:
            try:
                reviews, hit = future.result()
            except SerpApiError as exc:
                # Extra pages are nice to have; one failing shouldn't sink the inspection.
                log.warning("Optional review fetch (%s) failed: %s", source, exc)
                all_cached = False
                continue
            all_cached = all_cached and hit
            groups.append((source, reviews))
        return first, groups, all_cached

    def inspect(self, data_id: str) -> dict:
        data_id = (data_id or "").strip()
        if not DATA_ID_RE.match(data_id):
            raise InputError("That doesn't look like a building id.")

        first, groups, all_cached = self._fetch_reviews(data_id)
        reviews = scorer.merge_reviews(groups)
        info = first.get("place_info") or {}
        known = self.cache.get(f"place:{data_id}") or {}
        place = {
            "title": info.get("title") or known.get("title") or "This building",
            "address": info.get("address") or known.get("address") or "",
            "rating": _num(info.get("rating")) if info.get("rating") is not None else known.get("rating"),
            "reviews": _num(info.get("reviews"), int) if info.get("reviews") is not None else known.get("reviews"),
            "thumbnail": known.get("thumbnail") or "",
        }
        place["google_url"] = google_maps_url(place["title"], place["address"], known.get("place_id") or "")

        scored = scorer.score(
            reviews,
            self.signals,
            today=self.today(),
            topics=first.get("topics") or [],
            place_review_count=place["reviews"],
        )
        log.info(
            "inspect %s: %d reviews, verdict=%s risk=%s",
            data_id, scored["sample"]["reviews_used"], scored["verdict"], scored["huff_risk"],
        )

        place["rent"] = self._rent(place["title"])
        self._remember_on_map(data_id, place, known, scored)

        narration = self._narrate(place["title"], scored)
        comments = {t["theme"]: t["wolf_comment"] for t in narration.get("themes", [])}
        themes = [
            {
                "theme": t["theme"],
                "tone": t["tone"],
                "wolf_comment": comments.get(t["theme"], ""),
                "snippets": t["snippets"],
            }
            for t in scored["themes"]
        ]
        return {
            "data_id": data_id,
            "place": place,
            "verdict": scored["verdict"],
            "huff_risk": scored["huff_risk"],
            "sample": scored["sample"],
            "themes": themes,
            "signals_fired": scored["signals_fired"],
            "components": scored["components"],
            "narration": {
                "wolf_opening": narration["wolf_opening"],
                "verdict_line": narration["verdict_line"],
                "closing_threat": narration["closing_threat"],
                "source": narration.get("source", "canned"),
            },
            "cached": all_cached,
        }

    # ---- neighborhood map ----------------------------------------------------

    def _remember_on_map(self, data_id: str, place: dict, known: dict, scored: dict) -> None:
        """Pin this building on the map. Coordinates come from search, so share-link-only visits are skipped."""
        lat, lng = known.get("lat"), known.get("lng")
        if lat is None or lng is None or scored["verdict"] not in ("brick", "sticks", "straw"):
            return
        self.cache.set(f"inspected:{data_id}", {
            "data_id": data_id,
            "title": place["title"],
            "address": place["address"],
            "lat": lat,
            "lng": lng,
            "verdict": scored["verdict"],
            "huff_risk": scored["huff_risk"],
            "rent": place.get("rent"),
        })

    def _rent(self, title: str) -> dict | None:
        """One cached Google search per building. Rent is a nice-to-have, so failures never sink an inspection."""
        try:
            data, _ = self.client.rent_search(f'"{title}" Austin TX rent')
        except SerpApiError as exc:
            log.warning("rent lookup failed for %s: %s", title, exc)
            return None
        return parse_rent(data, title)

    def map(self) -> dict:
        buildings = sorted(self.cache.scan("inspected:"), key=lambda b: (b["huff_risk"], b["data_id"]))
        return {"buildings": buildings}

    def _narrate(self, title: str, scored: dict) -> dict:
        context = build_context(title, scored)
        key = "narration:" + hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
        hit = self.cache.get(key)
        if hit:
            return hit
        result = self.narrator.narrate(context)
        if result.get("source") == "gemini":  # only cache the good stuff; canned is free anyway
            self.cache.set(key, result)
        return result

    # ---- compare -----------------------------------------------------------

    def compare(self, ids: list[str]) -> dict:
        ids = list(dict.fromkeys(i.strip() for i in ids if i.strip()))
        if not 2 <= len(ids) <= MAX_COMPARE:
            raise InputError(f"Pick between 2 and {MAX_COMPARE} buildings to compare.")
        futures = [(i, self.compare_pool.submit(self.inspect, i)) for i in ids]
        results = []
        for data_id, future in futures:
            try:
                results.append(future.result())
            except (SerpApiError, InputError) as exc:
                results.append({"data_id": data_id, "error": str(exc)})
        scored = [r for r in results if isinstance(r.get("huff_risk"), int)]
        survivor = min(scored, key=lambda r: (r["huff_risk"], r["data_id"]))["data_id"] if scored else None
        return {"results": results, "survivor": survivor}
