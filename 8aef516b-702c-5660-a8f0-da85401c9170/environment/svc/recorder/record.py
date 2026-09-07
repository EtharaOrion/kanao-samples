"""Host-visible independent recorder; its mount is not available to the agent."""
import json
import time
import urllib.request
from pathlib import Path

SPEC = json.loads(Path("/opt/estate/scenario.json").read_text())
OUT = Path("/evidence/environment.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

while True:
    for service in SPEC["services"]:
        row = {"recorder_epoch": time.time(), "service": service}
        for route in ("/metrics", "/casebook"):
            try:
                with urllib.request.urlopen(f"http://{service}:8080{route}", timeout=3) as response:
                    row[route[1:]] = json.load(response)
            except Exception as exc:
                row[route[1:]] = {"error": type(exc).__name__}
        with OUT.open("a") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(json.dumps(row, separators=(",", ":")), flush=True)
    time.sleep(5)
