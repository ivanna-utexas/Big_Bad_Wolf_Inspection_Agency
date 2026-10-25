"""Offline stand-in for SerpApi (BBW_FAKE_SERPAPI=1).

Serves three made-up buildings so the UI can be built and demoed without
spending credits. None of this text comes from real reviews.
"""

from datetime import date, timedelta

import httpx

BUILDINGS = {
    "0xfa4e:0x1": ("Sturdy Oaks (fake)", 4.6, [
        ("Maintenance is super quick, they fixed my sink same day.", 5),
        ("Very clean and well maintained. Quiet floors for studying.", 5),
        ("I feel safe here, the building is secure at night.", 5),
        ("Rent went up a lot but the units are newly renovated.", 4),
        ("Parking is a nightmare on game days.", 3),
        ("Great location, the leasing office is helpful.", 5),
    ]),
    "0xfa4e:0x2": ("Twig Terrace (fake)", 3.4, [
        ("Walls are paper thin and the noise on weekends is wild.", 3),
        ("Maintenance was fast when my AC went out.", 4),
        ("Took weeks to fix the dishwasher.", 2),
        ("Decent spot, close to class. Elevator is always broken.", 3),
        ("Hidden fees at move-in, watch out.", 2),
        ("Fine for the price.", 4),
        ("Pool is nice, gym is small.", 4),
        ("Good enough for sophomore year.", 3),
        ("Close to Guadalupe, easy walk to class.", 4),
        ("Average apartment, friendly neighbors.", 4),
        ("Move-in went smoothly.", 4),
        ("Rooftop has a great view of the tower.", 4),
    ]),
    "0xfa4e:0x3": ("Haystack Heights (fake)", 2.1, [
        ("Black mold in the bathroom and a leak under the sink.", 1),
        ("Roaches everywhere. Maintenance never fixed anything.", 1),
        ("They kept my security deposit and blamed me for old carpet.", 1),
        ("Ceiling leaking again after the storm.", 2),
        ("Office staff were rude when I asked about the AC broken for a week.", 1),
        ("Car broken into in the garage, does not feel safe.", 1),
    ]),
}

# Varied endings so repeated fake reviews aren't exact duplicates.
OUTROS = ["Lived here two years.", "Signed for a second lease.", "Junior year here.", "Moved in this August.",
          "Would tell friends.", "Mixed feelings overall.", "Leaving after this year.", "Grad student here."]

REPLY = "We're sorry to hear this. Please reach out to our office so we can make it right."


def _reviews(data_id: str, offset: int, count: int) -> list[dict]:
    _, _, texts = BUILDINGS[data_id]
    today = date.today()
    out = []
    for i in range(offset, offset + count):
        text, rating = texts[i % len(texts)]
        out.append({
            "review_id": f"{data_id}-{i}",
            "snippet": f"{text} {OUTROS[i % len(OUTROS)]}" if i >= len(texts) else text,
            "rating": rating,
            "iso_date": (today - timedelta(days=4 + i * 9)).isoformat() + "T12:00:00Z",
            "likes": i % 5,
            **({"response": {"snippet": REPLY}} if rating <= 2 else {}),
        })
    return out


def _handler(request: httpx.Request) -> httpx.Response:
    p = dict(request.url.params)
    engine = p.get("engine")
    if engine == "google_maps":
        return httpx.Response(200, json={"local_results": [
            {"title": title, "data_id": did, "rating": rating, "reviews": 180 + i * 40,
             "address": f"{2400 + i * 100} Rio Grande St, Austin, TX", "type": "Apartment complex",
             "gps_coordinates": {"latitude": 30.2866 + i * 0.0018, "longitude": -97.7452 - i * 0.0012}}
            for i, (did, (title, rating, _)) in enumerate(BUILDINGS.items())
        ]})
    if engine == "google_maps_autocomplete":
        return httpx.Response(200, json={"suggestions": [
            {"value": title, "subtext": "West Campus, Austin, TX"} for title, _, _ in BUILDINGS.values()
        ]})
    if engine == "google_maps_reviews":
        did = p.get("data_id", "")
        if did not in BUILDINGS:
            return httpx.Response(200, json={"error": "Google hasn't returned any results for this query."})
        if p.get("query"):
            reviews = [r for r in _reviews(did, 0, 24) if p["query"].lower() in r["snippet"].lower()][:8]
            return httpx.Response(200, json={"reviews": reviews})
        if p.get("next_page_token"):
            return httpx.Response(200, json={"reviews": _reviews(did, 8, 20)})
        if p.get("sort_by") == "ratingLow":
            return httpx.Response(200, json={"reviews": sorted(_reviews(did, 0, 24), key=lambda r: r["rating"])[:8]})
        title, rating, _ = BUILDINGS[did]
        return httpx.Response(200, json={
            "place_info": {"title": title, "address": "Rio Grande St, Austin, TX", "rating": rating, "reviews": 200},
            "topics": [{"keyword": "maintenance", "mentions": 30, "id": "t1"}],
            "reviews": _reviews(did, 0, 8),
            "serpapi_pagination": {"next_page_token": "fake-token"},
        })
    if engine == "google":  # rent lookup: one listing-site result per building, with a price range
        for i, (title, _, _) in enumerate(BUILDINGS.values()):
            if title in p.get("q", ""):
                low, high = 750 + i * 350, 1400 + i * 500
                return httpx.Response(200, json={"organic_results": [{
                    "title": f"{title} - Rio Grande St Austin, TX", "source": "Apartments.com",
                    "snippet": f"{title} has rent from ${low:,}/mo. to ${high:,}/mo.",
                    "rich_snippet": {"top": {"detected_extensions": {"price_from": low, "price_to": high}}},
                }]})
        return httpx.Response(200, json={"organic_results": []})
    return httpx.Response(400, json={"error": "Unsupported engine in fake mode."})


def fake_http_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler))
