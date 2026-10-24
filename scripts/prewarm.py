"""Run the full inspect pipeline for well-known buildings so the demo is instant.

Usage:
    python scripts/prewarm.py                 # every building in data/prewarm.json
    python scripts/prewarm.py "26 West" ...   # just these

Each new building costs about 9 SerpApi searches the first time
(1 maps search + 3 review pages + 5 keyword probes). After that it is cached
in cache.sqlite3 and costs nothing.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATA_DIR  # noqa: E402
from app.main import build_inspector  # noqa: E402
from app.pipeline import InputError  # noqa: E402
from app.serpapi_client import SerpApiError  # noqa: E402


def main(names: list[str]) -> int:
    if not names:
        names = json.loads((DATA_DIR / "prewarm.json").read_text())["buildings"]
    inspector = build_inspector()
    failures = 0
    for name in names:
        start = time.time()
        try:
            candidates = inspector.search(name)["candidates"]
            if not candidates:
                print(f"  ?  {name}: no match on Google Maps")
                failures += 1
                continue
            top = candidates[0]
            result = inspector.inspect(top["data_id"])
        except (SerpApiError, InputError) as exc:
            print(f"  !  {name}: {exc}")
            failures += 1
            continue
        risk = result["huff_risk"] if result["huff_risk"] is not None else "--"
        source = "cache" if result["cached"] else "live"
        print(
            f"  ok {name} -> {top['title']}: {result['verdict']} ({risk}/100), "
            f"{result['sample']['reviews_used']} reviews, {source}, {time.time() - start:.1f}s"
        )
        print(f"     share: /?id={top['data_id']}")
    print(f"\nDone. {len(names) - failures}/{len(names)} buildings warmed.")
    return 1 if failures == len(names) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
