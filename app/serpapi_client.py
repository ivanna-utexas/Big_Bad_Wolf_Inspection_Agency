"""SerpApi client with a persistent cache and safe error handling.

Engines used:
  - google_maps               find the building
  - google_maps_reviews       pull reviews (newest, lowest-rated, keyword probes)
  - google_maps_autocomplete  search-box suggestions
"""

import logging
from typing import Any

import httpx

from .cache import Cache, make_key

log = logging.getLogger("bbw.serpapi")

BASE_URL = "https://serpapi.com/search.json"

# Fields we actually read; json_restrictor trims everything else off the payload.
RESTRICT = {
    "google_maps": (
        # Keep 10 so there are still a few left after non-Austin / non-residential places are filtered out.
        "local_results[0:10].{title,data_id,place_id,rating,reviews,address,type,types,gps_coordinates,thumbnail},"
        "place_results.{title,data_id,place_id,rating,reviews,address,type,types,gps_coordinates,thumbnail},error"
    ),
    "google": "organic_results[0:10].{title,snippet,source,displayed_link,rich_snippet},error",
    "google_maps_reviews": (
        # Keep `reviews` whole: sub-selecting its fields made SerpApi drop the list entirely.
        "place_info,topics,reviews,serpapi_pagination,error"
    ),
}

# SerpApi reports "nothing found" as an error string; we treat it as an empty result.
EMPTY_MARKERS = ("hasn't returned any results", "no results")


class SerpApiError(Exception):
    """A user-safe error. The message never contains the API key or request URL."""

    def __init__(self, message: str, kind: str = "upstream"):
        super().__init__(message)
        self.kind = kind  # "config" | "rate_limit" | "upstream" | "network"


class SerpApiClient:
    def __init__(
        self,
        api_key: str,
        cache: Cache,
        use_restrictor: bool = True,
        http: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.cache = cache
        self.use_restrictor = use_restrictor
        self.http = http or httpx.Client(timeout=30)

    # Returns (payload, from_cache).
    def get(self, params: dict[str, Any]) -> tuple[dict, bool]:
        key = make_key("serpapi", params)
        hit = self.cache.get(key)
        if hit is not None:
            return hit, True

        if not self.api_key:
            raise SerpApiError("SERPAPI_API_KEY is not set on the server.", kind="config")

        request = dict(params)
        restrictor = RESTRICT.get(params.get("engine", "")) if self.use_restrictor else None
        if restrictor:
            request["json_restrictor"] = restrictor

        data = self._fetch(request)
        error = data.get("error") if isinstance(data, dict) else None
        if error and restrictor and "restrictor" in str(error).lower():
            request.pop("json_restrictor", None)
            data = self._fetch(request)
            error = data.get("error")

        if error:
            if any(marker in str(error).lower() for marker in EMPTY_MARKERS):
                data = {"empty": True}
            else:
                log.warning("SerpApi error for engine=%s: %s", params.get("engine"), error)
                raise SerpApiError(str(error))

        self.cache.set(key, data)
        return data, False

    def _fetch(self, params: dict[str, Any]) -> dict:
        try:
            resp = self.http.get(BASE_URL, params={**params, "api_key": self.api_key})
        except httpx.HTTPError as exc:
            # The exception text can include the URL (and key), so don't pass it on.
            log.warning("SerpApi network error: %s", type(exc).__name__)
            raise SerpApiError("Could not reach SerpApi.", kind="network") from None

        if resp.status_code == 429:
            raise SerpApiError("SerpApi rate limit or credit limit reached.", kind="rate_limit")
        if resp.status_code == 401:
            raise SerpApiError("SerpApi rejected the API key.", kind="config")
        try:
            data = resp.json()
        except ValueError:
            raise SerpApiError(f"SerpApi returned a non-JSON response ({resp.status_code}).") from None
        if not isinstance(data, dict):
            raise SerpApiError("SerpApi returned an unexpected payload.")
        if resp.status_code >= 400 and "error" not in data:
            raise SerpApiError(f"SerpApi returned HTTP {resp.status_code}.")
        return data

    # ---- engine helpers -------------------------------------------------

    def maps_search(self, query: str) -> tuple[dict, bool]:
        return self.get({"engine": "google_maps", "type": "search", "q": query, "hl": "en"})

    def reviews(
        self,
        data_id: str,
        sort_by: str = "newestFirst",
        next_page_token: str | None = None,
        query: str | None = None,
    ) -> tuple[dict, bool]:
        params: dict[str, Any] = {
            "engine": "google_maps_reviews",
            "data_id": data_id,
            "hl": "en",
            "sort_by": sort_by,
        }
        # SerpApi ignores `num` on the first page, so only send it with a token.
        if next_page_token:
            params["next_page_token"] = next_page_token
            params["num"] = 20
        if query:
            params["query"] = query
        return self.get(params)

    def rent_search(self, query: str) -> tuple[dict, bool]:
        # Plain Google search: listing sites (Apartments.com, Redfin...) show rent ranges in their results.
        return self.get({"engine": "google", "q": query, "hl": "en", "gl": "us"})

    def autocomplete(self, query: str) -> tuple[dict, bool]:
        return self.get(
            {
                "engine": "google_maps_autocomplete",
                "q": query,
                "hl": "en",
                # Center suggestions on West Campus.
                "ll": "@30.2862,-97.7470,15z",
            }
        )
