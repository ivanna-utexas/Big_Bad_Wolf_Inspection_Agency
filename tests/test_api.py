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
                {"title": "Good Place", "data_id": GOOD_ID, "place_id": "ChIJgood", "rating": 4.6, "reviews": 120, "address": "1 Rio St, Austin, TX", "thumbnail": "t.jpg", "type": "Apartment complex"},
                {"title": "Coffee Spot", "data_id": "0x1:0x5", "address": "3 Rio St, Austin, TX", "type": "Coffee shop"},
                {"title": "Bad Place", "data_id": BAD_ID, "rating": 2.1, "reviews": 300, "address": "2 Rio St", "type": "Student housing center",
                 "gps_coordinates": {"latitude": 30.29, "longitude": -97.74}},
                {"title": "Dallas Lofts", "data_id": "0x1:0x6", "type": "Apartment building",
                 "gps_coordinates": {"latitude": 32.78, "longitude": -96.80}},
                {"title": "No id", "type": "Apartment complex", "address": "Austin, TX"},
                {"title": "Tiny Place", "data_id": TINY_ID, "rating": 5, "reviews": 3, "address": "Austin, TX", "types": ["Condominium complex"]},
                {"title": "Fourth", "data_id": "0x1:0x4", "address": "Austin, TX", "type": "Apartment complex"},
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
        if engine == "google":
            if "Good Place" not in p["q"]:
                return httpx.Response(200, json={"organic_results": []})
            return httpx.Response(200, json={"organic_results": [
                {"title": "Average rent in Austin", "source": "RentHop", "snippet": "Austin rentals average $1,285 to $2,995."},
                {"title": "Good Place - 1 Rio St Austin, TX", "source": "Apartments.com", "snippet": "Rent from $800/mo.",
                 "rich_snippet": {"top": {"detected_extensions": {"price_from": 800.0, "price_to": 1650.0}}}},
            ]})
        if engine == "google_maps_autocomplete":
            return httpx.Response(200, json={"suggestions": [{"value": "Castilian", "subtext": "Austin, TX"}, {"value": "Castilian Dallas", "subtext": "Dallas, TX"}, {"subtext": "no value"}]})
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


def test_api_key_never_logged(setup, caplog):
    import logging

    import app.main  # noqa: F401  (configures logging levels)

    api, _ = setup
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("httpx").setLevel(logging.WARNING)
        api.get("/api/search", params={"q": "castilian"})
        api.get("/api/inspect", params={"data_id": BAD_ID})
    assert "secret-key" not in caplog.text


def test_inspect_links_to_google_maps(setup):
    api, _ = setup
    api.get("/api/search", params={"q": "castilian"})
    url = api.get("/api/inspect", params={"data_id": GOOD_ID}).json()["place"]["google_url"]
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "query_place_id=ChIJgood" in url


def test_compare_limits(setup):
    api, _ = setup
    assert api.get("/api/compare", params={"ids": GOOD_ID}).status_code == 400
    too_many = ",".join(f"0x1:0x{i}" for i in range(11))
    assert api.get("/api/compare", params={"ids": too_many}).status_code == 400


def test_map_shows_inspected_homes_with_coordinates(setup):
    api, _ = setup
    empty = api.get("/api/map").json()
    assert empty["buildings"] == [] and "{z}" in empty["tile_url"]
    api.get("/api/search", params={"q": "castilian"})
    api.get("/api/inspect", params={"data_id": BAD_ID})
    api.get("/api/inspect", params={"data_id": GOOD_ID})  # no GPS from search -> not pinned
    buildings = api.get("/api/map").json()["buildings"]
    assert [b["data_id"] for b in buildings] == [BAD_ID]
    bad = buildings[0]
    assert (bad["lat"], bad["lng"]) == (30.29, -97.74)
    assert bad["verdict"] == "straw" and bad["title"] == "Bad Place"


def test_exact_match_with_list_type_is_kept():
    # Google's single exact match (place_results) gives `type` as a list, not a string.
    from app.pipeline import _candidate

    item = {"title": "Villas on Rio", "data_id": "0x1:0x7", "type": ["Apartment complex"],
            "address": "2111 Rio Grande St, Austin, TX 78705", "gps_coordinates": {"latitude": 30.285, "longitude": -97.7448}}
    c = _candidate(item)
    assert c is not None and c["type"] == "Apartment complex"


@pytest.mark.parametrize("title,type_,kept", [
    ("Jester West Dormitory", ["Building"], True),
    ("San Jacinto Residence Hall", "University department", True),
    ("Kinsolving Residence Hall", ["Student dormitory"], True),
    ("Robert A. Welch Hall", "Academic department", False),
    ("Kins Dining", "Restaurant", False),
])
def test_dorms_and_residence_halls_are_kept(title, type_, kept):
    from app.pipeline import _candidate

    item = {"title": title, "data_id": "0x1:0x8", "type": type_, "address": "Austin, TX"}
    assert (_candidate(item) is not None) == kept


def test_inspect_includes_rent_range(setup):
    api, calls = setup
    api.get("/api/search", params={"q": "castilian"})
    body = api.get("/api/inspect", params={"data_id": GOOD_ID}).json()
    # The generic "average rent in Austin" result is ignored; only the building's own listing counts.
    assert body["place"]["rent"] == {"low": 800, "high": 1650, "source": "Apartments.com"}
    assert any(c["engine"] == "google" and c["q"] == '"Good Place" Austin TX rent' for c in calls)
    assert api.get("/api/inspect", params={"data_id": BAD_ID}).json()["place"]["rent"] is None


def test_parse_rent_from_snippets():
    from app.pipeline import parse_rent

    data = {"organic_results": [
        {"source": "Facebook · The Castilian Residence Hall", "title": "Floor plans",
         "snippet": "Rates as low as $1,199! 565 Sq.Ft. / $775+ and $500 off your first month. $75 app fee."},
        {"source": "UT Austin", "title": "Castilian rates", "snippet": "Rate per person $14,465 per year"},
        {"source": "ApartmentAdvisor", "title": "Average rent in Austin", "snippet": "1-bedroom $1,200."},
    ]}
    assert parse_rent(data, "The Castilian") == {"low": 775, "high": 1199, "source": "Facebook"}
    assert parse_rent({"organic_results": []}, "The Castilian") is None
