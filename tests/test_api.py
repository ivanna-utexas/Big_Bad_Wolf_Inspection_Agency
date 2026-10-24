import httpx
import pytest
from fastapi.testclient import TestClient

from app import scorer
from app.cache import Cache
from app.config import DATA_DIR
from app.main import create_app
from app.narrator import Narrator, canned, validate
from app.pipeline import Inspector
from app.serpapi_client import SerpApiClient

from .fixtures import TODAY, bad_building, good_building

GOOD_ID = "0x8644b5a0:0x1111"
BAD_ID = "0x8644b5a0:0x2222"
TINY_ID = "0x8644b5a0:0x3333"


def fake_serpapi(calls: list):
    """An httpx transport that pretends to be SerpApi."""

    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p)
        engine = p.get("engine")
        if p.get("q") == "boom Austin TX":
            return httpx.Response(200, json={"error": "Invalid API key. Your API key should be here"})
        if p.get("q") == "ratelimit Austin TX":
            return httpx.Response(429, json={"error": "Your account has run out of searches."})
        if engine == "google_maps":
            if p["q"].startswith("nothing"):
                return httpx.Response(200, json={"error": "Google hasn't returned any results for this query."})
            return httpx.Response(200, json={"local_results": [
                {"title": "Good Place", "data_id": GOOD_ID, "rating": 4.6, "reviews": 120, "address": "1 Rio St", "thumbnail": "t.jpg"},
                {"title": "Bad Place", "data_id": BAD_ID, "rating": 2.1, "reviews": 300, "address": "2 Rio St"},
                {"title": "No id"},
                {"title": "Tiny Place", "data_id": TINY_ID, "rating": 5, "reviews": 3},
                {"title": "Fourth", "data_id": "0x1:0x4"},
            ]})
        if engine == "google_maps_reviews":
            did = p["data_id"]
            if did == TINY_ID:
                return httpx.Response(200, json={"place_info": {"title": "Tiny Place", "reviews": 3}, "reviews": good_building(3)})
            reviews = good_building(30) if did == GOOD_ID else bad_building(30)
            if p.get("query"):
                return httpx.Response(200, json={"reviews": reviews[:4]})
            if p.get("next_page_token"):
                return httpx.Response(200, json={"reviews": reviews[8:28]})
            if p.get("sort_by") == "ratingLow":
                return httpx.Response(500, text="oops")  # optional page fails: should not sink the inspection
            return httpx.Response(200, json={
                "place_info": {"title": "Good Place" if did == GOOD_ID else "Bad Place", "address": "Rio St", "rating": 4, "reviews": 200},
                "topics": [{"keyword": "roaches", "mentions": 5, "id": "t1"}],
                "reviews": reviews[:8],
                "serpapi_pagination": {"next_page_token": "tok"},
            })
        if engine == "google_maps_autocomplete":
            return httpx.Response(200, json={"suggestions": [{"value": "Castilian", "subtext": "Austin, TX"}, {"subtext": "no value"}]})
        return httpx.Response(400, json={"error": "unknown"})

    return httpx.MockTransport(handler)


@pytest.fixture
def setup(tmp_path):
    calls: list = []
    cache = Cache(tmp_path / "c.sqlite3")
    signals, probes = scorer.load_signals(DATA_DIR / "signals.json")
    client = SerpApiClient("secret-key", cache, http=httpx.Client(transport=fake_serpapi(calls)))
    inspector = Inspector(client, cache, Narrator(""), signals, probes, today=lambda: TODAY)
    return TestClient(create_app(inspector)), calls


def test_search_returns_top_three_candidates(setup):
    api, calls = setup
    body = api.get("/api/search", params={"q": "castilian"}).json()
    assert [c["data_id"] for c in body["candidates"]] == [GOOD_ID, BAD_ID, TINY_ID]
    assert calls[0]["q"] == "castilian Austin TX"
    assert calls[0]["api_key"] == "secret-key"


def test_search_empty_results(setup):
    api, _ = setup
    r = api.get("/api/search", params={"q": "nothing here"})
    assert r.status_code == 200 and r.json() == {"candidates": []}


def test_search_errors_are_friendly_and_hide_key(setup):
    api, _ = setup
    r = api.get("/api/search", params={"q": "boom"})
    assert r.status_code == 502
    assert "secret-key" not in r.text and "wolf" in r.json()["error"].lower()
    r = api.get("/api/search", params={"q": "ratelimit"})
    assert r.status_code == 429


def test_short_query_rejected(setup):
    api, _ = setup
    assert api.get("/api/search", params={"q": "a"}).status_code == 400


def test_inspect_full_payload(setup):
    api, calls = setup
    api.get("/api/search", params={"q": "castilian"})
    body = api.get("/api/inspect", params={"data_id": BAD_ID}).json()
    assert body["verdict"] == "straw"
    assert 0 <= body["huff_risk"] <= 100
    assert body["place"]["title"] == "Bad Place"
    assert body["sample"]["reviews_used"] >= 15 and body["sample"]["oldest"] <= body["sample"]["newest"]
    assert len(body["themes"]) == 3 and all(t["wolf_comment"] for t in body["themes"])
    assert body["signals_fired"] and body["narration"]["verdict_line"]
    assert body["cached"] is False
    assert "Reviewer Person" not in str(body)
    engines = {c["engine"] for c in calls}
    assert {"google_maps", "google_maps_reviews"} <= engines
    probes = [c["query"] for c in calls if c.get("query")]
    assert len(probes) == 5


def test_inspect_second_time_is_fully_cached(setup):
    api, calls = setup
    api.get("/api/inspect", params={"data_id": GOOD_ID})
    n = len(calls)
    body = api.get("/api/inspect", params={"data_id": GOOD_ID}).json()
    # Every successful call is cached; only the failing ratingLow page is retried.
    assert len(calls) - n == 1
    assert body["verdict"] == "brick"


def test_inspect_not_enough_data(setup):
    api, _ = setup
    body = api.get("/api/inspect", params={"data_id": TINY_ID}).json()
    assert body["verdict"] == "not_enough_data"
    assert body["huff_risk"] is None
    assert body["narration"]["verdict_line"]


def test_inspect_rejects_bad_id(setup):
    api, calls = setup
    assert api.get("/api/inspect", params={"data_id": "x&api_key=evil"}).status_code == 400
    assert not calls


def test_compare_picks_survivor(setup):
    api, _ = setup
    body = api.get("/api/compare", params={"ids": f"{GOOD_ID},{BAD_ID}"}).json()
    assert body["survivor"] == GOOD_ID
    assert len(body["results"]) == 2


def test_autocomplete(setup):
    api, _ = setup
    body = api.get("/api/autocomplete", params={"q": "cast"}).json()
    assert body == {"suggestions": [{"value": "Castilian", "subtext": "Austin, TX"}]}


def test_missing_serpapi_key_is_friendly(tmp_path):
    cache = Cache(tmp_path / "c.sqlite3")
    signals, probes = scorer.load_signals(DATA_DIR / "signals.json")
    inspector = Inspector(SerpApiClient("", cache), cache, Narrator(""), signals, probes)
    r = TestClient(create_app(inspector)).get("/api/search", params={"q": "castilian"})
    assert r.status_code == 502 and "badge" in r.json()["error"]


# ---- narrator -----------------------------------------------------------

CTX = {
    "title": "Bad Place", "verdict": "straw", "huff_risk": 80,
    "themes": [{"theme": "Pests", "snippet_ids": ["r1", "r2"]}, {"theme": "Noise", "snippet_ids": ["r3"]}],
}


def test_validate_accepts_good_output_and_drops_invented_ids():
    raw = {
        "wolf_opening": "Huff.", "verdict_line": "Straw.", "closing_threat": "Bye.",
        "themes": [
            {"theme": "Pests", "wolf_comment": "Eek.", "snippet_ids": ["r1", "r999"]},
            {"theme": "Noise", "wolf_comment": "Loud.", "snippet_ids": ["r3"]},
        ],
    }
    out = validate(raw, CTX)
    assert out["themes"][0]["snippet_ids"] == ["r1"]


@pytest.mark.parametrize("raw", [
    None, [], {"wolf_opening": "x"},
    {"wolf_opening": "a", "verdict_line": "b", "closing_threat": "c", "themes": [{"theme": "Pests", "wolf_comment": "x"}]},
    {"wolf_opening": "", "verdict_line": "b", "closing_threat": "c", "themes": []},
])
def test_validate_rejects_bad_output(raw):
    assert validate(raw, CTX) is None


def test_narrator_retries_once_then_falls_back():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not json"}]}}]})

    n = Narrator("key", http=httpx.Client(transport=httpx.MockTransport(handler)))
    out = n.narrate(CTX)
    assert len(attempts) == 2
    assert out["source"] == "canned" and len(out["themes"]) == 2


def test_canned_is_deterministic():
    assert canned(CTX) == canned(CTX)
