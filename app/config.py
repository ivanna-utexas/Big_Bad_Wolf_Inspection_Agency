"""Settings read from environment variables (and an optional .env file)."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"

load_dotenv(ROOT / ".env")


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CACHE_PATH = Path(os.getenv("BBW_CACHE_PATH", str(ROOT / "cache.sqlite3")))
AUTOCOMPLETE_ENABLED = _flag("BBW_AUTOCOMPLETE", True)
JSON_RESTRICTOR_ENABLED = _flag("BBW_JSON_RESTRICTOR", True)
