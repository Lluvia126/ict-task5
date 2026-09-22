import json
from pathlib import Path

COUNTER_FILE = Path("data/counter.json")
COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)


def next_announcement_id() -> str:
    if COUNTER_FILE.exists():
        data = json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
    else:
        data = {"counter": 0}
    data["counter"] += 1
    COUNTER_FILE.write_text(json.dumps(data), encoding="utf-8")
    return f"ANN_{data['counter']:06d}"