"""FastAPI app: JSON API under /api, static frontend at /."""

import logging

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, scorer
from .cache import Cache
from .narrator import Narrator
from .pipeline import Inspector, InputError
from .serpapi_client import SerpApiClient, SerpApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
# httpx logs full request URLs at INFO, and SerpApi URLs carry the API key.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

WOLF_ERRORS = {
    "config": "The wolf lost his inspector badge (the server is missing its SerpApi key).",
    "rate_limit": "The wolf is out of breath. Too many inspections at once; try again in a minute.",
    "network": "The wolf can't reach the forest right now. Try again in a moment.",
    "upstream": "The wolf tripped over a root on the way there. Try again in a moment.",
}


def build_inspector() -> Inspector:
    signals, probes = scorer.load_signals(config.DATA_DIR / "signals.json")
    if config.FAKE_SERPAPI:
        # Offline mode: synthetic buildings, separate cache so real data never mixes in.
        from .fake_serpapi import fake_http_client

        cache = Cache(config.CACHE_PATH.with_name("cache-fake.sqlite3"))
        client = SerpApiClient("fake", cache, http=fake_http_client())
    else:
        cache = Cache(config.CACHE_PATH)
        client = SerpApiClient(config.SERPAPI_API_KEY, cache, use_restrictor=config.JSON_RESTRICTOR_ENABLED)
    return Inspector(
        client=client,
        cache=cache,
        narrator=Narrator(config.GEMINI_API_KEY, config.GEMINI_MODEL),
        signals=signals,
        probes=probes,
        autocomplete_enabled=config.AUTOCOMPLETE_ENABLED,
    )


def create_app(inspector: Inspector | None = None) -> FastAPI:
    app = FastAPI(title="Big Bad Wolf Inspection Agency")
    app.state.inspector = inspector or build_inspector()

    def run(fn, *args):
        try:
            return fn(*args)
        except InputError as exc:
            return JSONResponse({"error": str(exc), "kind": "input"}, status_code=400)
        except SerpApiError as exc:
            status = 429 if exc.kind == "rate_limit" else 502
            return JSONResponse({"error": WOLF_ERRORS.get(exc.kind, WOLF_ERRORS["upstream"]), "kind": exc.kind}, status_code=status)
        except Exception:
            logging.getLogger("bbw").exception("Unexpected error")
            return JSONResponse({"error": "The wolf sneezed. Something broke on our end.", "kind": "server"}, status_code=500)

    @app.get("/api/search")
    def search(q: str = Query("", max_length=200)):
        return run(app.state.inspector.search, q)

    @app.get("/api/autocomplete")
    def autocomplete(q: str = Query("", max_length=200)):
        return run(app.state.inspector.autocomplete, q)

    @app.get("/api/inspect")
    def inspect(data_id: str = Query("", max_length=100)):
        return run(app.state.inspector.inspect, data_id)

    @app.get("/api/compare")
    def compare(ids: str = Query("", max_length=1000)):
        return run(app.state.inspector.compare, ids.split(","))

    @app.get("/api/map")
    def neighborhood_map():
        result = run(app.state.inspector.map)
        if isinstance(result, dict):
            result["tile_url"] = config.MAP_TILE_URL
        return result

    @app.get("/api/health")
    def health():
        return {"ok": True, "serpapi_key": bool(config.SERPAPI_API_KEY), "fake_serpapi": config.FAKE_SERPAPI, "llm": bool(config.GEMINI_API_KEY)}

    @app.get("/")
    def index():
        return FileResponse(config.FRONTEND_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
    return app


app = create_app()
