# 🐺 Big Bad Wolf Inspection Agency

*HackTX 2026, theme: fairytales.*

Type the name of an apartment complex near UT Austin. The Big Bad Wolf, now a licensed (satirical) building inspector, reads what Google Maps reviewers say and tells you whether the place is **Brick** (solid), **Sticks** (shaky), or **Straw** (blows right over). You get a 0–100 huff-and-puff meter, the three biggest review themes with short anonymized quotes, and an animation where the matching pig house stands or falls.

> **Satire based on what reviewers say, not facts about the building.**

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example          # then add SERPAPI_API_KEY (and GEMINI_API_KEY if you have one)
.venv/bin/uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

No SerpApi key yet, or saving credits? Run with three made-up buildings:

```bash
BBW_FAKE_SERPAPI=1 .venv/bin/uvicorn app.main:app --reload --port 8000
```

Run the tests:

```bash
.venv/bin/python -m pytest -q
```

## Environment variables

| Variable | Required | What it does |
|---|---|---|
| `SERPAPI_API_KEY` | yes | SerpApi key. Read only by the backend; never sent to the browser or written to logs. |
| `GEMINI_API_KEY` | no | Gemini key for the wolf's lines. Without it, the wolf uses canned lines. |
| `GEMINI_MODEL` | no | Comma-separated models; the retry moves to the next one. Defaults to `gemini-3-flash-preview,gemini-3.8-flash` (`gemini-2.5-flash` is closed to new keys). |
| `BBW_MAP_TILE_URL` | no | Map background tile URL for the neighborhood map, e.g. your CARTO URL with its key. Must contain `{z}`, `{x}`, `{y}`. Sent to the browser, so the key is public; restrict it to your domain in CARTO. Without it, CARTO's tiles show "API key required". |
| `BBW_CACHE_PATH` | no | SQLite cache file. Defaults to `cache.sqlite3` in the repo root. |
| `BBW_AUTOCOMPLETE` | no | `0` turns off search-box suggestions (each suggestion lookup is a SerpApi search). |
| `BBW_JSON_RESTRICTOR` | no | `0` asks SerpApi for full payloads instead of trimmed ones. |
| `BBW_FAKE_SERPAPI` | no | `1` serves synthetic buildings with no network calls (uses `cache-fake.sqlite3`). |

## Prewarm before the demo

Judges will mostly try the same famous West Campus buildings. Warm them up so they load instantly and cost nothing during judging:

```bash
.venv/bin/python scripts/prewarm.py                    # everything in data/prewarm.json
.venv/bin/python scripts/prewarm.py "The Castilian"    # or specific names
```

Edit `data/prewarm.json` to change the list. Each new building costs about 10 SerpApi searches once, then it's cached forever in `cache.sqlite3`. The script prints a share link (`/?id=...`) for each building. Check that each name matched the right building.

## How SerpApi is used

Three SerpApi engines, all called from the backend:

1. **`google_maps`** (`type=search`, `q="<name> Austin TX"`): finds the building. We drop anything outside Austin (GPS box, or "Austin" in the address) or not residential (place type mentions apartment, condo, housing, dorm, townhouse, etc.) and return the top 3 remaining `local_results` so the user confirms the right one. A single exact match (`place_results`) is handled too.
2. **`google_maps_reviews`** with `data_id`, called several ways per building:
   - `sort_by=newestFirst`, 2 pages (paginated with `next_page_token`): a representative, current sample. Page one also gives `place_info` and `topics` (Google's keyword chips with mention counts), which feed the score.
   - `sort_by=ratingLow`, 1 page: the strongest complaints.
   - `query=<keyword>` probes for `mold`, `leak`, `roach`, `maintenance`, `deposit` (editable in `data/signals.json`): finds reviews that mention specific problems even if they aren't recent or low-rated.
3. **`google_maps_autocomplete`**: search-box suggestions centered on West Campus, keeping only ones whose subtext mentions Austin.
4. **`google`** (`q='"<name>" Austin TX rent'`): monthly rent range. Uses listing sites' structured price range (e.g. Apartments.com `price_from`/`price_to`), otherwise $300–$6,000 amounts from snippets that name the building. Generic "average rent in Austin" pages are ignored. Shown on the result page and used by the map's rent filter.

`json_restrictor` trims every maps and reviews response down to the fields we read. Every response is cached in SQLite, keyed by engine + params (never the key), so repeats are free. Budget: about 10 searches per new building.

Errors are handled at every step: SerpApi's `error` field, "no results" (treated as empty, not as a failure), HTTP 401/429, network failures, and junk payloads all become friendly wolf messages. If an optional page (ratingLow, a probe, page two) fails, the inspection continues without it.

## Architecture

```
Browser (frontend/: plain HTML + CSS + JS, inline SVG animation)
  │  GET /api/search?q=...          -> up to 3 candidate buildings
  │  GET /api/inspect?data_id=...   -> verdict payload
  │  GET /api/compare?ids=a,b,c     -> compare 2-10 homes from My List, who survives
  │  GET /api/map                   -> every inspected home with GPS + verdict (neighborhood map, /?map=1)
  │  GET /api/autocomplete?q=...    -> suggestions
  ▼
FastAPI (app/)
  ├─ serpapi_client.py   SerpApi calls + persistent cache + safe errors
  ├─ cache.py            SQLite key/value cache
  ├─ scorer.py           deterministic huff_risk (pure functions, unit tested)
  ├─ narrator.py         Gemini writes the wolf's lines; strict JSON, validated, 1 retry, canned fallback
  ├─ pipeline.py         search / inspect / compare orchestration
  └─ fake_serpapi.py     offline synthetic data (BBW_FAKE_SERPAPI=1)
data/signals.json        the editable phrase dictionary
data/prewarm.json        buildings to warm before the demo
scripts/prewarm.py
tests/                   scorer + API + narrator tests (no network)
```

The scorer decides the verdict. The LLM only narrates it and never sees or changes the number logic.

### Scoring in short

`huff_risk = 40 (base) + signals + stars + topics + owner replies`, clamped to 0–100.

- **Signals:** each review is matched against `data/signals.json` (e.g. mold +10, roaches +10, maintenance ignored +7, thin walls +3, responsive maintenance −6, quiet −3). Simple negation is handled ("never had any roaches" doesn't count). Each review is weighted by **recency** (1.0 for the last 3 months, down to 0.3 at 3+ years), **engagement** (`1 + min(likes, 10) × 0.05`), and **source** (low-rated and keyword-probe reviews count 0.6× because they over-sample complaints). Signals = 7 × (weighted sum ÷ total review weight).
- **Stars:** `(3.5 − recency-weighted average of the newest reviews) × 12`.
- **Topics:** Google's topic chips that match a signal (e.g. "roaches · 14 mentions"), scaled by total review count, capped at ±8.
- **Owner replies:** +5 if replies to negative reviews are near copy-paste, −3 if they're specific and varied.
- **Verdict:** under 35 brick, 35–65 sticks, over 65 straw. Fewer than 15 usable reviews returns `not_enough_data`.

All constants are at the top of `app/scorer.py`. The "Why this verdict?" drawer in the UI shows every phrase that fired, how many reviews used it, its weight, and its impact.

## Safeguards

- Visible disclaimer on every page: "Satire based on what reviewers say, not facts about the building."
- Every verdict shows its sample size and date range.
- Reviewer names, profile links, and photos are dropped as soon as the SerpApi response is parsed. Snippets are trimmed to about 200 characters, and a best-effort scrubber replaces staff names ("manager Sarah" → "manager [name]"), emails, and phone numbers.
- Duplicate review text is counted once.
- The narrator prompt says "reviewers say", forbids invented facts and claims about specific people, and may only reference snippet IDs we provide. Output is validated, and anything unknown is dropped.
- The API key stays on the server. Request URLs (which contain the key) are never logged, and error messages never echo them. Review text is not logged.
- `data_id` input is validated before it reaches SerpApi.

## Decisions on the OPEN items

- **Backend:** Python + FastAPI, with a plain HTML/CSS/JS frontend served by the same app. No build step.
- **LLM:** Google Gemini (`gemini-3-flash-preview`, falling back to `gemini-3.8-flash`; set by `GEMINI_MODEL`) through its REST API with JSON output mode. This also fits the MLH Gemini track. Canned lines take over if there's no key, an error, or invalid JSON after one retry. Good narrations are cached.
- **Hosting:** run locally for the demo. A `Dockerfile` is included for a Vultr (or any) VM: `docker build -t bbw . && docker run -p 80:8000 --env-file .env -v $PWD/cache:/app/cache bbw`. Point the .TECH domain at the VM. Copy your warmed `cache.sqlite3` into the mounted `cache/` folder so the hosted demo is warm too.
- **Signal weights:** starting values are in `data/signals.json` and `app/scorer.py`, set against synthetic fixtures. **They still need tuning against 5–10 real buildings.** Run `scripts/prewarm.py`, open the "why" drawer for each one, and adjust until the verdicts feel fair and funny. The main levers are `SIGNAL_SCALE`, `STAR_SCALE`, and the individual weights.
- **Snippet IDs** are short hashes of SerpApi's `review_id`, so no Google identifiers reach the browser.
- **`huff_risk` is `null`** when the verdict is `not_enough_data`. The API contract showed a number, but null is clearer than a fake 0.
- **The `cached` flag** is `true` when every SerpApi call for that inspection came from our cache. Scores are recomputed on every request (it's cheap), so editing `signals.json` takes effect immediately with no cache wipe.
- **Extra response fields** beyond the contract: `data_id`, `themes[].tone` (good/bad), `signals_fired[].theme` / `.impact`, `components` (score breakdown), and `narration.source` (`gemini` or `canned`).

## Stretch goals included

- **My List**: tap “♡ Add to list” on any result to save it. The heart-on-house button (top left) opens My List (top right), where you can reopen or remove homes. **Compare locations** sends up to 10 of them to `/api/compare`, which inspects them in parallel and crowns the survivor. The list lives in the browser's localStorage, so it's per device.
- Crumbling houses: straw (left) collapses into a pile with straw blown across the grass, sticks buckle and drop their roof, brick (right, next to the wolf) stands. The meter runs straw (red, left) to brick (green, right) and fills from the brick end as risk rises.
- A link to the building's Google Maps listing (pinned with its `place_id`), plus a share button that copies `/?id=<data_id>` (uses the native share sheet on phones when copying isn't allowed).
- Search-box autocomplete (third SerpApi engine).
- Not added: sound effects.
