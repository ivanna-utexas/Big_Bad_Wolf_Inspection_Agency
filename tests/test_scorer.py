from datetime import timedelta

import pytest

from app import scorer
from app.config import DATA_DIR

from .fixtures import TODAY, bad_building, good_building, neutral_building, raw_review

SIGNALS, PROBES = scorer.load_signals(DATA_DIR / "signals.json")


def run(raws, source="newest", **kw):
    reviews = scorer.merge_reviews([(source, raws)])
    return scorer.score(reviews, SIGNALS, today=TODAY, **kw)


def test_signals_file_loads_and_has_probes():
    assert len(SIGNALS) > 10
    assert 4 <= len(PROBES) <= 5


def test_bad_building_is_straw():
    result = run(bad_building())
    assert result["verdict"] == "straw"
    assert result["huff_risk"] > scorer.STRAW_ABOVE


def test_good_building_is_brick():
    result = run(good_building())
    assert result["verdict"] == "brick"
    assert result["huff_risk"] < scorer.BRICK_BELOW


def test_neutral_building_is_sticks():
    assert run(neutral_building())["verdict"] == "sticks"


def test_not_enough_data():
    result = run(bad_building(scorer.MIN_REVIEWS - 1))
    assert result["verdict"] == "not_enough_data"
    assert result["huff_risk"] is None
    assert result["sample"]["reviews_used"] == scorer.MIN_REVIEWS - 1


def test_deterministic():
    assert run(bad_building()) == run(bad_building())


def test_huff_risk_is_clamped():
    raws = [raw_review(i, f"mold leak roaches bed bugs rats flood unsafe break-in, day {i}", 1) for i in range(30)]
    assert run(raws)["huff_risk"] == 100
    raws = [raw_review(i, f"maintenance is super quick, very clean, quiet, secure, well maintained, year {i}", 5) for i in range(30)]
    assert run(raws)["huff_risk"] == 0


@pytest.mark.parametrize("risk,verdict", [(0, "brick"), (34, "brick"), (35, "sticks"), (65, "sticks"), (66, "straw"), (100, "straw")])
def test_verdict_thresholds(risk, verdict):
    assert scorer.verdict_for(risk) == verdict


def test_recency_weight():
    assert scorer.recency_weight(TODAY - timedelta(days=30), TODAY) == 1.0
    assert scorer.recency_weight(TODAY - timedelta(days=2000), TODAY) == 0.3
    mid = scorer.recency_weight(TODAY - timedelta(days=500), TODAY)
    assert 0.3 < mid < 1.0
    assert scorer.recency_weight(None, TODAY) == 0.5


def test_engagement_weight_caps_at_ten_likes():
    assert scorer.engagement_weight(0) == 1.0
    assert scorer.engagement_weight(10) == pytest.approx(1.5)
    assert scorer.engagement_weight(500) == pytest.approx(1.5)


def test_old_complaints_count_less_than_new_ones():
    filler = [raw_review(100 + i, f"Fine place, unit {i}.", 4) for i in range(20)]
    new = run(filler + [raw_review(i, f"Mold and roaches in unit {i}.", 4, days_ago=10) for i in range(5)])
    old = run(filler + [raw_review(i, f"Mold and roaches in unit {i}.", 4, days_ago=1500) for i in range(5)])
    assert new["huff_risk"] > old["huff_risk"]


def test_negation_is_ignored():
    filler = [raw_review(100 + i, f"Fine place, unit {i}.", 4) for i in range(20)]
    negated = run(filler + [raw_review(1, "Never had any roaches and no mold at all.", 4)])
    terms = {s["term"] for s in negated["signals_fired"]}
    assert "roaches" not in terms and "mold / mildew" not in terms


def test_themes_top_three_with_short_snippets():
    result = run(bad_building())
    assert len(result["themes"]) == 3
    for theme in result["themes"]:
        assert 1 <= len(theme["snippets"]) <= scorer.SNIPPETS_PER_THEME
        for snip in theme["snippets"]:
            assert len(snip["text"]) <= scorer.SNIPPET_CHARS + 2  # plus ellipses
            assert "Reviewer Person" not in snip["text"]
            assert snip["id"].startswith("r")


def test_signals_fired_explains_score():
    result = run(bad_building())
    fired = {s["term"]: s for s in result["signals_fired"]}
    assert fired["mold / mildew"]["count"] >= 3
    assert fired["mold / mildew"]["weight"] > 0
    assert all({"term", "count", "weight"} <= set(s) for s in result["signals_fired"])


def test_copy_paste_replies_are_penalized():
    result = run(bad_building())
    assert result["components"]["owner_replies"] == scorer.COPY_PASTE_PENALTY
    assert any(s["term"] == "copy-paste owner replies" for s in result["signals_fired"])


def test_varied_replies_get_a_bonus():
    replies = [
        "Thanks for flagging the dishwasher, our tech replaced the pump on Tuesday.",
        "Sorry about the parking gate. A new sensor was installed and towing rules changed.",
        "We had pest control treat units on floor four and will recheck monthly.",
    ]
    raws = [raw_review(i, f"It's fine for the price, unit {i}.", 3) for i in range(20)]
    raws += [raw_review(50 + i, f"Had a problem in unit {i}.", 2, response=r) for i, r in enumerate(replies)]
    assert run(raws)["components"]["owner_replies"] == -scorer.VARIED_REPLY_BONUS


def test_supplemental_sources_count_less():
    base = [raw_review(100 + i, f"Fine place, unit {i}.", 4) for i in range(20)]
    extra = [raw_review(i, f"Mold everywhere in unit {i}.", 1) for i in range(5)]
    as_newest = scorer.score(scorer.merge_reviews([("newest", base + extra)]), SIGNALS, today=TODAY)
    as_probe = scorer.score(scorer.merge_reviews([("newest", base), ("probe", extra)]), SIGNALS, today=TODAY)
    assert as_probe["components"]["signals"] < as_newest["components"]["signals"]


def test_merge_dedupes_and_drops_reviewer_identity():
    raws = bad_building(5)
    merged = scorer.merge_reviews([("newest", raws), ("low", raws)])
    assert len(merged) == 5
    assert all(r["source"] == "newest" for r in merged)
    assert all("user" not in r for r in merged)


def test_topics_nudge_score():
    topics = [{"keyword": "roaches", "mentions": 40}]
    with_topics = run(neutral_building(), topics=topics, place_review_count=200)
    assert with_topics["components"]["topics"] > 0
    assert with_topics["huff_risk"] > run(neutral_building())["huff_risk"]


def test_redact_names_and_contacts():
    text = "The manager Sarah Jones was rude. Ask for Mike. Email me@x.com or call 512-555-1234. Mr. Smith"
    out = scorer.redact(text)
    for leaked in ("Sarah", "Mike", "me@x.com", "555-1234", "Smith"):
        assert leaked not in out


def test_excerpt_centers_on_match():
    text = ("Lovely pool. " * 30) + "But there was mold in the closet." + (" Nice gym." * 30)
    out = scorer.excerpt(text, text.index("mold"))
    assert "mold" in out and out.startswith("…") and out.endswith("…")
    assert len(out) <= scorer.SNIPPET_CHARS + 2


def test_empty_and_junk_reviews_do_not_crash():
    raws = [{}, {"snippet": ""}, {"snippet": "ok", "rating": "bad", "likes": None, "iso_date": "garbage"}]
    result = run(raws)
    assert result["verdict"] == "not_enough_data"


def test_duplicate_text_counts_once():
    raws = [raw_review(i, "Same exact review text.", 1) for i in range(20)]
    assert len(scorer.merge_reviews([("newest", raws)])) == 1
