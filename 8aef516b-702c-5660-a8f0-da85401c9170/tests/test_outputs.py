
import hashlib
import hmac
import json
import math
from pathlib import Path


import pytest

def _read_json(path: str):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None



def _read_lines(path: str) -> list[str]:
    try:
        return [line.strip() for line in Path(path).read_text().splitlines()
                if line.strip()]
    except OSError:
        return []

SPEC = {
    "mechanism": "coordinated-omission",
    "services": [
        "synthetic-probe", "reservation-edge", "admission-gate", "reservation-worker",
        "availability-cache", "inventory", "confirmation", "user-wait-log",
    ],
    "allowed_labels": [
        "capacity-cliff", "probe-population-gap", "cpu-saturation", "queue-backlog",
        "slow-query", "input-scaling", "load-shedding", "none",
    ],
    "expected_findings": [["reservation-worker", "capacity-cliff"]],
    "required_ruleouts": ["availability-cache", "admission-gate"],
    "verdict_path": "/app/verdict.txt",
    "evidence_path": "/app/monitor_evidence.json",
    "minimum_span_sec": 45,
}
_EXPECTED_SET = {tuple(item) for item in SPEC["expected_findings"]}
_SERVICES = set(SPEC["services"])
_LABELS = set(SPEC["allowed_labels"])
_LINES = _read_lines(SPEC["verdict_path"])
_REPORTED = {tuple(line.split(":")) for line in _LINES if line.count(":") == 1}
_EVIDENCE_VALUE = _read_json(SPEC["evidence_path"])
_EVIDENCE = _EVIDENCE_VALUE if isinstance(_EVIDENCE_VALUE, dict) else {}
_ANOMALIES_VALUE = _EVIDENCE.get("anomalies")
_ANOMALIES = _ANOMALIES_VALUE if isinstance(_ANOMALIES_VALUE, list) else []
_KEY = b"c74956feb7446c9f91dffcba100c68d5ad648dbc96bde12ca0aeda736f7078e1"

def _format_valid() -> bool:
    return (
        bool(_LINES)
        and _LINES == sorted(set(_LINES))
        and len(_REPORTED) == len(_LINES)
        and all(service in _SERVICES and label in _LABELS - {"none"} for service, label in _REPORTED)
    )

def _entry(finding: tuple[str, str]):
    found = [row for row in _ANOMALIES if isinstance(row, dict) and (row.get("service"), row.get("class")) == finding]
    return found[0] if len(found) == 1 else None

def _authentic(observation) -> bool:
    if not isinstance(observation, dict) or not _KEY or observation.get("service") not in _SERVICES:
        return False
    supplied = observation.get("signature")
    if not isinstance(supplied, str):
        return False
    body = {key: value for key, value in observation.items() if key != "signature"}
    try:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        return False
    wanted = hmac.new(_KEY, encoded, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, wanted)

def _observations(entry) -> list[dict]:
    rows = entry.get("observations") if isinstance(entry, dict) else None
    return rows if isinstance(rows, list) else []

def _supports(finding: tuple[str, str]) -> bool:
    entry = _entry(finding)
    rows = _observations(entry)
    return finding in _REPORTED and bool(rows) and all(_authentic(row) for row in rows)

def _window_valid(finding: tuple[str, str]) -> bool:
    entry = _entry(finding)
    if not _supports(finding):
        return False
    times = sorted({float(row["uptime_sec"]) for row in _observations(entry)
                    if row.get("service") == finding[0]
                    and isinstance(row.get("uptime_sec"), (int, float))
                    and not isinstance(row.get("uptime_sec"), bool)})
    return len(times) >= 3 and times[-1] - times[0] >= SPEC["minimum_span_sec"]

def _evidence_complete() -> bool:
    pairs = [(row.get("service"), row.get("class")) for row in _ANOMALIES if isinstance(row, dict)]
    return (bool(_REPORTED)
            and len(pairs) == len(_ANOMALIES) == len(set(pairs))
            and set(pairs) == _REPORTED)

def _all_observations_authentic() -> bool:
    rows = []
    for entry in _ANOMALIES:
        rows.extend(_observations(entry))
    for entry in _EVIDENCE.get("ruled_out", []) if isinstance(_EVIDENCE.get("ruled_out"), list) else []:
        rows.extend(_observations(entry))
    return bool(rows) and all(_authentic(row) for row in rows)

def _has_fabricated_observation() -> bool:
    rows = []
    for entry in _ANOMALIES:
        rows.extend(_observations(entry))
    for entry in _EVIDENCE.get("ruled_out", []) if isinstance(_EVIDENCE.get("ruled_out"), list) else []:
        rows.extend(_observations(entry))
    return any(not _authentic(row) for row in rows)

def _required_ruleouts_valid() -> bool:
    entries = _EVIDENCE.get("ruled_out")
    if not isinstance(entries, list):
        return False
    by_service = {entry.get("service"): entry for entry in entries if isinstance(entry, dict)}
    for service in SPEC["required_ruleouts"]:
        entry = by_service.get(service)
        if not entry or not isinstance(entry.get("reason"), str) or len(entry["reason"].strip()) < 20:
            return False
        rows = _observations(entry)
        if len(rows) < 2 or not all(_authentic(row) for row in rows):
            return False
        times = sorted({row.get("uptime_sec") for row in rows if row.get("service") == service and isinstance(row.get("uptime_sec"), (int, float))})
        if len(times) < 2 or times[-1] - times[0] < SPEC["minimum_span_sec"] / 2:
            return False
    return True

def _no_phantoms() -> bool:
    return _REPORTED == _EXPECTED_SET

def _phantom_pairs() -> list[tuple[str, str]]:
    return sorted(_REPORTED - _EXPECTED_SET)

def _casebooks(entry, service=None) -> list[dict]:
    return [row for row in _observations(entry)
            if row.get("kind") == "casebook" and (service is None or row.get("service") == service)]

def _event_time(events: list[dict], name: str):
    values = [event.get("at") for event in events if event.get("event") == name and isinstance(event.get("at"), (int, float))]
    return min(values) if values else None

def _population_calculations_valid(finding: tuple[str, str]) -> bool:
    entry = _entry(finding)
    if not _supports(finding):
        return False
    intervals = [item for book in _casebooks(entry) for item in book.get("intervals", [])]
    unique = {item.get("interval"): item for item in intervals}.values()
    ordered = sorted(unique, key=lambda row: row.get("interval", -1))
    calculations = entry.get("calculations")
    if len(ordered) < 4 or not isinstance(calculations, dict):
        return False
    first, latest = ordered[0], ordered[-1]
    try:
        selected_fraction_first = first["actual_closed_loop_sends"] / first["scheduled_open_loop"]
        selected_fraction_latest = latest["actual_closed_loop_sends"] / latest["scheduled_open_loop"]
    except (KeyError, TypeError, ZeroDivisionError):
        return False
    return (math.isclose(calculations.get("selected_fraction_first", -1),
                         selected_fraction_first, rel_tol=0.01, abs_tol=0.0001)
            and math.isclose(calculations.get("selected_fraction_latest", -1),
                             selected_fraction_latest, rel_tol=0.01, abs_tol=0.0001))

def _causal_valid(finding: tuple[str, str]) -> bool:
    entry = _entry(finding)
    if not _supports(finding) or not isinstance(entry.get("reason"), str) or len(entry["reason"].strip()) < 40:
        return False
    mechanism = SPEC["mechanism"]

    if mechanism == "hedge-commit-race":
        books = _casebooks(entry)
        cases = [case for book in books for case in book.get("cases", [])]
        duplicates = []
        controls = []
        for case in cases:
            settlements = case.get("settlement_ids", [])
            events = case.get("events", [])
            if case.get("accepted_operations") == 1 and case.get("successful_calls") == 1 and len(set(settlements)) >= 2:
                cancel = _event_time(events, "cancel-sent")
                commits = sorted(event.get("at") for event in events if event.get("event") == "settlement-commit")
                if cancel is not None and len(commits) >= 2 and cancel < commits[-1]:
                    duplicates.append(case.get("operation_id"))
            if case.get("region") == "domestic" and len(set(settlements)) == 1:
                controls.append(case.get("operation_id"))
        reconciliations = [book for book in books if book.get("cohort_reconciliation") and book.get("fleet_rollup")]
        if not reconciliations:
            return False
        latest = reconciliations[-1]
        cohorts = {(row.get("region"), row.get("review_mode")): row
                   for row in latest["cohort_reconciliation"]}
        affected = cohorts.get(("cross-border", "manual"), {})
        control = cohorts.get(("domestic", "automatic"), {})
        changes = latest.get("change_history", [])
        harmless_risk_change = any(row.get("component") == "risk-gateway"
                                   and row.get("scope") == "all payout traffic"
                                   and row.get("summary") == "signature library refresh" for row in changes)
        return (len(set(duplicates)) >= 2 and len(set(controls)) >= 2
                and affected.get("durable_settlements", 0) > affected.get("accepted_operations", 0)
                and control.get("durable_settlements") == control.get("accepted_operations")
                and latest["fleet_rollup"].get("settlement_excess_fraction", 1) < 0.01
                and harmless_risk_change)

    if mechanism == "autoscaler-readiness":
        cycles = [cycle for book in _casebooks(entry) for cycle in book.get("cycles", [])]
        unique = {cycle.get("cycle"): cycle for cycle in cycles}.values()
        ordered = sorted(unique, key=lambda row: row.get("cycle", -1))
        loader_books = _casebooks(entry, "feature-loader")
        loader = loader_books[-1] if loader_books else {}
        note = loader.get("inherited_runbook_note", {})
        batches = loader.get("load_batches", [])
        return (len(ordered) >= 4
                and all(row.get("excluded_pods") for row in ordered[-4:])
                and all(row.get("desired_replicas", 99) < row.get("desired_if_stably_ready", 0) for row in ordered[-4:])
                and ordered[-1].get("queue_oldest_age_sec", 0) > ordered[-4].get("queue_oldest_age_sec", 0)
                and all(row.get("cpu_pressure_full", 1) < 0.05 for row in ordered[-4:])
                and note.get("verification_state") == "unverified"
                and len(batches) >= 4
                and all(row.get("work_units_completed", 0) > 0
                        and row.get("cpu_pressure_full", 1) < 0.05 for row in batches[-4:]))

    if mechanism == "tail-sampler-inversion" and finding[0] == "trace-gateway":
        books = _casebooks(entry, "trace-gateway")
        if not books:
            return False
        latest = books[-1]
        strata = latest.get("strata", [])
        helper = latest.get("helper_summary", {})
        probs = {row.get("inclusion_probability") for row in strata}
        fraud = [row for row in strata if row.get("service") == "fraud-check"]
        if len(fraud) != 2 or len(probs) < 2:
            return False
        raw_error = next(row["retained"] for row in fraud if row["outcome"] == "error") / sum(row["retained"] for row in fraud)
        weighted_error = next(row["retained"] / row["inclusion_probability"] for row in fraud if row["outcome"] == "error") / sum(row["retained"] / row["inclusion_probability"] for row in fraud)
        calculations = entry.get("calculations", {})
        return (raw_error > 0.20 and weighted_error < 0.01
                and helper.get("status") == "green"
                and helper.get("selected_rows", 0) > 0
                and helper.get("selection") == "retained trace records"
                and helper.get("probability_weighting") == "none"
                and math.isclose(calculations.get("fraud_raw_error_fraction", -1), raw_error, rel_tol=0.03)
                and math.isclose(calculations.get("fraud_weighted_error_fraction", -1), weighted_error, rel_tol=0.03))

    if mechanism == "tail-sampler-inversion" and finding[0] == "price-engine":
        books = _casebooks(entry, "price-engine")
        if not books:
            return False
        matrix = books[-1].get("probe_matrix", [])
        lookup = {(row.get("account"), row.get("item_count"), row.get("padding_bytes")): row.get("duration_ms") for row in matrix}
        small = lookup.get(("wholesale", 10, 4000))
        large_small_bytes = lookup.get(("wholesale", 40, 250))
        large_large_bytes = lookup.get(("wholesale", 40, 4000))
        return (all(isinstance(value, (int, float)) for value in (small, large_small_bytes, large_large_bytes))
                and large_small_bytes > small * 2.5
                and abs(large_large_bytes - large_small_bytes) < small)

    if mechanism == "stream-transaction-boundary":
        cases = [case for book in _casebooks(entry) for case in book.get("cases", [])]
        affected = []
        controls = []
        for case in cases:
            events = case.get("events", [])
            bank = _event_time(events, "bank-accepted")
            abort = _event_time(events, "transaction-abort")
            rewind = _event_time(events, "offset-rewind")
            bank_events = sorted((event for event in events if event.get("event") == "bank-accepted"),
                                 key=lambda event: event.get("at", 0))
            restart = next((event for event in events if event.get("event") == "process-restart"), {})
            reordered_ingestion = (len(bank_events) >= 2
                                   and bank_events[0].get("ingested_at", 0) > bank_events[1].get("ingested_at", 0))
            epoch_changes = (restart.get("from_epoch") and restart.get("to_epoch")
                             and restart.get("from_epoch") != restart.get("to_epoch")
                             and bank_events and bank_events[0].get("process_epoch") == restart.get("from_epoch")
                             and bank_events[-1].get("process_epoch") == restart.get("to_epoch"))
            if (len(set(case.get("bank_transfer_ids", []))) >= 2
                    and case.get("read_committed_output_records") == 1
                    and None not in (bank, abort, rewind) and bank < abort < rewind
                    and reordered_ingestion and epoch_changes):
                affected.append(case.get("refund_id"))
            if case.get("review_mode") == "automatic" and len(set(case.get("bank_transfer_ids", []))) == 1:
                controls.append(case.get("refund_id"))
        return len(set(affected)) >= 2 and len(set(controls)) >= 2

    if mechanism == "coordinated-omission":
        books = _casebooks(entry)
        intervals = [item for book in books for item in book.get("intervals", [])]
        unique = {item.get("interval"): item for item in intervals}.values()
        ordered = sorted(unique, key=lambda row: row.get("interval", -1))
        if len(ordered) < 4:
            return False
        first = ordered[0]
        latest = ordered[-1]
        recent = ordered[-4:]
        summaries = [book.get("dashboard_summary") for book in books if book.get("dashboard_summary")]
        dashboard = summaries[-1] if summaries else {}
        controls = [book.get("single_room_control") for book in books if book.get("single_room_control")]
        control = controls[-1] if controls else {}
        return (latest["actual_closed_loop_sends"] < first["actual_closed_loop_sends"]
                and latest["closed_loop_send_to_response_p99_ms"] < first["closed_loop_send_to_response_p99_ms"]
                and latest["open_loop_scheduled_to_completion_p99_ms"] > first["open_loop_scheduled_to_completion_p99_ms"]
                and latest["queue_oldest_age_sec"] > first["queue_oldest_age_sec"]
                and latest["effective_worker_slots"] < first["effective_worker_slots"]
                and latest["effective_worker_slots"] < control.get("worker_slots", 0)
                and latest["completed"] < first["completed"]
                and all(right["open_loop_scheduled_to_completion_p99_ms"]
                        > left["open_loop_scheduled_to_completion_p99_ms"]
                        for left, right in zip(recent, recent[1:]))
                and all(right["queue_oldest_age_sec"] > left["queue_oldest_age_sec"]
                        for left, right in zip(recent, recent[1:]))
                and all(right["effective_worker_slots"] <= left["effective_worker_slots"]
                        for left, right in zip(ordered, ordered[1:]))
                and dashboard.get("http_status") == 200
                and dashboard.get("status") == "green"
                and dashboard.get("selected_rows", 0) < dashboard.get("expected_scheduled_arrivals", 0))
    return False

EXPECTED = [tuple(item) for item in SPEC["expected_findings"]]
PHANTOM_ASSERTION = ("__no_phantom__", "none")
PHANTOMS = _phantom_pairs() or [PHANTOM_ASSERTION]


def test_format_and_known_vocabulary():
    assert _format_valid()


@pytest.mark.parametrize("finding", EXPECTED, ids=lambda pair: ":".join(pair))
def test_gate_expected_finding(finding):
    assert _supports(finding)


@pytest.mark.parametrize("finding", EXPECTED, ids=lambda pair: ":".join(pair))
def test_causal_discriminator(finding):
    assert _causal_valid(finding)


@pytest.mark.parametrize("finding", EXPECTED, ids=lambda pair: ":".join(pair))
def test_required_population_calculations(finding):
    assert _population_calculations_valid(finding)


@pytest.mark.parametrize("finding", EXPECTED, ids=lambda pair: ":".join(pair))
def test_evidence_window(finding):
    assert _window_valid(finding)


def test_all_observations_authentic():
    assert _all_observations_authentic()


def test_required_alternatives_ruled_out():
    assert _required_ruleouts_valid()


def test_evidence_matches_verdict_exactly():
    assert _evidence_complete()


def test_gate_no_phantom_findings():
    assert _no_phantoms()


@pytest.mark.parametrize("finding", PHANTOMS, ids=lambda pair: ":".join(pair))
def test_negative_phantom_finding(finding):
    assert finding != PHANTOM_ASSERTION


def test_negative_fabricated_observation():
    assert _has_fabricated_observation()
