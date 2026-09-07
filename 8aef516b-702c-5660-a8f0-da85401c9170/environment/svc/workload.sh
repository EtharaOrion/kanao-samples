#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import threading
import time
import urllib.request
from pathlib import Path

SPEC = json.loads(Path("/opt/estate/scenario.json").read_text())
COMBINATIONS = [dict(zip(SPEC["fields"], values)) for values in itertools.product(*SPEC["fields"].values())]


def drive(service: str, offset: int) -> None:
    index = offset
    while True:
        payload = COMBINATIONS[index % len(COMBINATIONS)]
        index += 1
        try:
            request = urllib.request.Request(
                f"http://{service}:8080/probe",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=4) as response:
                response.read()
        except Exception:
            pass
        time.sleep(0.65)


for position, service in enumerate(SPEC["services"]):
    threading.Thread(target=drive, args=(service, position), daemon=True).start()

while True:
    time.sleep(30)
