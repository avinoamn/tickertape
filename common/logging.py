"""One JSON object per line to stdout."""
import json
import sys
from datetime import UTC, datetime


def log(svc: str, **fields) -> None:
    rec = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "svc": svc, **fields}
    sys.stdout.write(json.dumps(rec, default=str) + "\n")
    sys.stdout.flush()
