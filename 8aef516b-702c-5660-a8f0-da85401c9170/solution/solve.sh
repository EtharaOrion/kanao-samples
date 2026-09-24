#!/bin/bash
set -euo pipefail
cat > /app/reference-spec.json <<'REFERENCE_SPEC'
{"id":"monitoring__reservation-admission-latency","title":"Reservation probes improve while completion declines","mechanism":"coordinated-omission","domain":"reservation admission and capacity","incident":"Synthetic send-to-response p99 and CPU both improve after admission shedding begins, yet users report long waits before requests enter the platform and completed reservations decline. Determine whether the apparent recovery is real and identify the faulty owner using the appropriate request population and timing boundary.","services":["synthetic-probe","reservation-edge","admission-gate","reservation-worker","availability-cache","inventory","confirmation","user-wait-log"],"allowed_labels":["capacity-cliff","probe-population-gap","cpu-saturation","queue-backlog","slow-query","input-scaling","load-shedding","none"],"expected_findings":[["reservation-worker","capacity-cliff"]],"required_ruleouts":["availability-cache","admission-gate"],"evidence_sources":["synthetic-probe","reservation-worker","admission-gate","availability-cache"],"fields":{"reservation_class":["single-room","group"],"probe_mode":["closed-loop","open-loop"]},"casebook_owners":["synthetic-probe","reservation-worker","admission-gate"],"agent_window_sec":60,"finding_explanations":{"reservation-worker:capacity-cliff":"Group degradation is a reservation-worker capacity cliff: effective_worker_slots decline while closed_loop_send_to_response_p99_ms falls because closed-loop probes offer less work, but open_loop_scheduled_to_completion_p99_ms, queue age, and user waiting continue to rise as completions fall."},"decoy_explanations":{"availability-cache":"Its CPU follows completed productive work and full CPU pressure remains low.","admission-gate":"It enforces the published bounded admission policy and preserves scheduled, admitted, rejected, and completed identities."},"verdict_path":"/app/verdict.txt","evidence_path":"/app/monitor_evidence.json","minimum_span_sec":45,"required_calculations":{"selected_fraction_first":"actual_closed_loop_sends divided by scheduled_open_loop for the earliest cited interval","selected_fraction_latest":"actual_closed_loop_sends divided by scheduled_open_loop for the latest cited interval"}}
REFERENCE_SPEC
cat > /app/reference-solver.py <<'REFERENCE_PROGRAM'
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

SPEC = json.loads(Path("/app/reference-spec.json").read_text())


def fetch(service: str, route: str) -> dict:
    with urllib.request.urlopen(f"http://{service}:8080{route}", timeout=5) as response:
        return json.load(response)


deadline = time.monotonic() + 45
for service in SPEC["services"]:
    while True:
        try:
            fetch(service, "/health")
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)

observed = {service: [] for service in SPEC["evidence_sources"]}
for sample_index in range(5):
    for service in SPEC["evidence_sources"]:
        observed[service].append(fetch(service, "/metrics"))
        observed[service].append(fetch(service, "/casebook"))
    if sample_index < 4:
        time.sleep(15)

entries = []
for service, label in SPEC["expected_findings"]:
    sources = list(dict.fromkeys([service] + SPEC["evidence_sources"]))
    rows = [row for source in sources for row in observed.get(source, [])]
    entry = {
        "service": service,
        "class": label,
        "reason": SPEC["finding_explanations"][f"{service}:{label}"],
        "observations": rows,
    }
    if SPEC["mechanism"] == "tail-sampler-inversion" and service == "trace-gateway":
        latest = [row for row in observed["trace-gateway"] if row.get("kind") == "casebook"][-1]
        fraud = [row for row in latest["strata"] if row["service"] == "fraud-check"]
        raw = next(row["retained"] for row in fraud if row["outcome"] == "error") / sum(row["retained"] for row in fraud)
        weighted = next(row["retained"] / row["inclusion_probability"] for row in fraud if row["outcome"] == "error") / sum(row["retained"] / row["inclusion_probability"] for row in fraud)
        entry["calculations"] = {"fraud_raw_error_fraction": raw, "fraud_weighted_error_fraction": weighted}
    if SPEC["mechanism"] == "coordinated-omission":
        books = [row for row in rows if row.get("kind") == "casebook" and row.get("intervals")]
        merged = {item["interval"]: item for book in books for item in book["intervals"]}
        intervals = [merged[key] for key in sorted(merged)]
        first, latest = intervals[0], intervals[-1]
        entry["calculations"] = {
            "selected_fraction_first": first["actual_closed_loop_sends"] / first["scheduled_open_loop"],
            "selected_fraction_latest": latest["actual_closed_loop_sends"] / latest["scheduled_open_loop"],
        }
    entries.append(entry)

ruled_out = []
for service in SPEC["required_ruleouts"]:
    latest_metrics = next(
        row for row in reversed(observed[service]) if row.get("kind") == "metrics"
    )
    observed_values = (
        f"Observed requests_total={latest_metrics['requests_total']}, "
        f"errors_total={latest_metrics['errors_total']}, "
        f"durable_outcomes_total={latest_metrics['durable_outcomes_total']}, and "
        f"cpu_pressure_full={latest_metrics['cpu_pressure_full']}; these values "
        "support the class-specific clearance."
    )
    ruled_out.append({
        "service": service,
        "class": "none",
        "reason": f"{SPEC['decoy_explanations'][service]} {observed_values}",
        "observations": observed[service],
    })

evidence = {
    "anomalies": entries,
    "ruled_out": ruled_out,
    "method": "Repeated signed observations, lifecycle identity joins, matched controls, and the mechanism-specific causal discriminator.",
}
Path("/app/verdict.txt").write_text("\n".join(sorted(f"{service}:{label}" for service, label in SPEC["expected_findings"])) + "\n")
Path("/app/monitor_evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
print(json.dumps({"findings": SPEC["expected_findings"], "samples_per_source": 5, "span_sec": 60}))

REFERENCE_PROGRAM
python3 /app/reference-solver.py
