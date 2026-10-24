import os
import tempfile

# Keep tests away from the real cache and real API keys.
os.environ["BBW_CACHE_PATH"] = os.path.join(tempfile.mkdtemp(), "test-cache.sqlite3")
os.environ["SERPAPI_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
