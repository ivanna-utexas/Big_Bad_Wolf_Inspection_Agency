"""Fixture reviews in SerpApi's raw google_maps_reviews shape."""

from datetime import date, timedelta

TODAY = date(2026, 10, 24)


def raw_review(i: int, text: str, rating: int, days_ago: int = 10, likes: int = 0, response: str | None = None) -> dict:
    review = {
        "review_id": f"rid-{i}",
        "user": {"name": f"Reviewer Person {i}", "link": "https://example.com/u", "thumbnail": "x.png"},
        "snippet": text,
        "rating": rating,
        "iso_date": (TODAY - timedelta(days=days_ago)).isoformat() + "T12:00:00Z",
        "likes": likes,
    }
    if response:
        review["response"] = {"snippet": response}
    return review


BAD_TEXTS = [
    ("Black mold in the bathroom and a leak under the sink for months.", 1),
    ("Roaches everywhere. Maintenance never fixed anything.", 1),
    ("They kept my security deposit for no reason. Hidden fees everywhere.", 1),
    ("Ceiling leaking again, water damage on my desk.", 2),
    ("Saw roaches in the kitchen within a week of move-in.", 1),
    ("AC was broken for two weeks in August. Took weeks to fix.", 1),
    ("Office staff were rude and unprofessional.", 2),
    ("Car broken into in the garage. Feels unsafe at night.", 1),
    ("Walls are paper thin, constant noise from neighbors.", 2),
    ("Mold came back after they painted over it.", 1),
]

GOOD_TEXTS = [
    ("Maintenance is super quick, fixed my sink same day.", 5),
    ("Very clean and well maintained building.", 5),
    ("Quiet floors, great for studying.", 5),
    ("I feel safe here, the building is secure.", 5),
    ("Maintenance was fast and friendly every time.", 5),
    ("Newly renovated units with new appliances.", 4),
    ("Great location near campus, well-kept gym.", 5),
    ("Leasing office was helpful and kind.", 4),
    ("Spotless hallways, fixed same day when the dishwasher broke.", 5),
    ("Never had any roaches in two years.", 5),
]

COPY_PASTE = "We're sorry to hear about your experience. Please contact our office so we can make this right."


def bad_building(n: int = 30) -> list[dict]:
    return [
        raw_review(i, BAD_TEXTS[i % len(BAD_TEXTS)][0] + f" (#{i})", BAD_TEXTS[i % len(BAD_TEXTS)][1],
                   days_ago=5 + i * 7, likes=i % 4, response=COPY_PASTE)
        for i in range(n)
    ]


def good_building(n: int = 30) -> list[dict]:
    return [
        raw_review(i, GOOD_TEXTS[i % len(GOOD_TEXTS)][0] + f" (#{i})", GOOD_TEXTS[i % len(GOOD_TEXTS)][1],
                   days_ago=5 + i * 7)
        for i in range(n)
    ]


def neutral_building(n: int = 30) -> list[dict]:
    texts = ["Decent place, close to class.", "It's fine for the price.", "Okay apartment, nothing special."]
    return [raw_review(i, texts[i % 3] + f" (#{i})", 3 + (i % 2), days_ago=5 + i * 7) for i in range(n)]
