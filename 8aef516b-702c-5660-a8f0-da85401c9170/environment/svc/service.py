"""Deterministic multi-service observability estate for the five Opus 5 tasks."""
from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import math
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SPEC = json.loads(Path("/opt/estate/scenario.json").read_text())
NAME = os.environ["SERVICE_NAME"]
KEY = Path("/opt/estate/receipt.key").read_bytes().strip()
START = time.monotonic()
LOCK = threading.RLock()
STATE = {"requests": 0, "errors": 0}
LOG = Path("/var/log/svc") / f"{NAME}.log"
LOG.parent.mkdir(parents=True, exist_ok=True)


def uptime() -> float:
    return time.monotonic() - START


def signed(payload: dict) -> dict:
    body = dict(payload)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    body["signature"] = hmac.new(KEY, encoded, hashlib.sha256).hexdigest()
    return body


def log_event(kind: str, **fields) -> None:
    row = {"service": NAME, "kind": kind, "uptime_sec": round(uptime(), 6), **fields}
    with LOCK, LOG.open("a") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def base_metrics() -> dict:
    now = uptime()
    mechanism = SPEC["mechanism"]
    cpu_slope = 0.015
    latency = 18.0
    queue_depth = 0
    queue_age = 0.0
    full_pressure = 0.002
    some_pressure = 0.02
    eligible = True
    durable = int(now / 4)
    attempts = durable

    if mechanism == "hedge-commit-race":
        if NAME == "risk-gateway":
            cpu_slope, latency, some_pressure = 0.22, 76.0, 0.18
        if NAME == "settlement-writer":
            attempts = int(now / 3) * 2
            durable = int(now / 3) + int(now / 12)
    elif mechanism == "autoscaler-readiness":
        if NAME == "feature-loader":
            cpu_slope, latency, some_pressure = 0.28, 31.0, 0.25
        if NAME == "ranker-pool":
            queue_depth = 80 + int(now * 3.2)
            queue_age = 8 + now * 0.9
            eligible = int(now // 8) % 3 == 0
    elif mechanism == "tail-sampler-inversion":
        if NAME == "fraud-check":
            cpu_slope, latency = 0.08, 68.0
        if NAME == "price-engine":
            latency = 45 + now * 0.8
    elif mechanism == "stream-transaction-boundary":
        if NAME == "backfill-loader":
            queue_depth = max(0, 1800 - int(now * 22))
            queue_age = max(0.0, 240 - now * 3)
        if NAME == "refund-worker":
            durable = int(now / 6) + int(now / 18)
    elif mechanism == "coordinated-omission":
        if NAME == "availability-cache":
            cpu_slope = max(0.03, 0.20 - now * 0.0015)
            some_pressure = 0.16
        if NAME == "reservation-worker":
            queue_depth = 40 + int(now * 4)
            queue_age = 5 + now * 1.1
            latency = 110 + now * 2.2

    with LOCK:
        requests = STATE["requests"]
        errors = STATE["errors"]
    return signed({
        "kind": "metrics",
        "service": NAME,
        "uptime_sec": round(now, 6),
        "requests_total": requests,
        "errors_total": errors,
        "cpu_seconds_total": round(now * cpu_slope, 6),
        "latency_ms": round(latency, 6),
        "queue_depth": queue_depth,
        "queue_oldest_age_sec": round(queue_age, 6),
        "cpu_pressure_some": round(some_pressure, 6),
        "cpu_pressure_full": round(full_pressure, 6),
        "metric_eligible": eligible,
        "attempts_total": attempts,
        "durable_outcomes_total": durable,
        "process_epoch": int(now // 24),
    })


def hedge_casebook(now: float) -> dict:
    count = max(1, int(now // 9))
    cases = []
    for index in range(count):
        affected = index % 2 == 1
        base = index * 9.0
        events = [
            {"event": "accepted", "at": base + 0.1},
            {"event": "attempt-start", "attempt_id": f"a-{index}-1", "at": base + 0.2},
        ]
        settlements = [f"settlement-{index}-a"]
        if affected:
            events.extend([
                {"event": "attempt-start", "attempt_id": f"a-{index}-2", "at": base + 0.5},
                {"event": "call-success", "attempt_id": f"a-{index}-2", "at": base + 0.9},
                {"event": "cancel-sent", "attempt_id": f"a-{index}-1", "at": base + 1.0},
                {"event": "settlement-commit", "settlement_id": settlements[0], "attempt_id": f"a-{index}-2", "at": base + 1.1},
            ])
            if now >= base + 4.0:
                settlements.append(f"settlement-{index}-b")
                events.append({"event": "settlement-commit", "settlement_id": settlements[1], "attempt_id": f"a-{index}-1", "at": base + 3.8})
        else:
            events.extend([
                {"event": "call-success", "attempt_id": f"a-{index}-1", "at": base + 0.8},
                {"event": "settlement-commit", "settlement_id": settlements[0], "attempt_id": f"a-{index}-1", "at": base + 0.9},
            ])
        cases.append({
            "operation_id": f"payout-{index:03d}",
            "region": "cross-border" if affected else "domestic",
            "review_mode": "manual" if affected else "automatic",
            "accepted_operations": 1,
            "successful_calls": 1,
            "attempt_ids": [event["attempt_id"] for event in events if event["event"] == "attempt-start"],
            "settlement_ids": settlements,
            "events": events,
        })
    affected_excess = sum(max(0, len(set(case["settlement_ids"])) - case["accepted_operations"])
                          for case in cases if case["region"] == "cross-border")
    domestic_accepted = 12000 + int(now * 45)
    cross_border_accepted = 420 + int(now * 2)
    cohorts = [
        {"region": "domestic", "review_mode": "automatic", "accepted_operations": domestic_accepted,
         "durable_settlements": domestic_accepted, "sample_case_ids": [case["operation_id"] for case in cases if case["region"] == "domestic"]},
        {"region": "cross-border", "review_mode": "manual", "accepted_operations": cross_border_accepted,
         "durable_settlements": cross_border_accepted + affected_excess,
         "sample_case_ids": [case["operation_id"] for case in cases if case["region"] == "cross-border"]},
    ]
    fleet_accepted = sum(row["accepted_operations"] for row in cohorts)
    fleet_settled = sum(row["durable_settlements"] for row in cohorts)
    return {
        "cases": cases,
        "cohort_reconciliation": cohorts,
        "fleet_rollup": {"accepted_operations": fleet_accepted, "durable_settlements": fleet_settled,
                           "settlement_excess_fraction": round((fleet_settled - fleet_accepted) / fleet_accepted, 8)},
        "change_history": [
            {"change_id": "chg-441", "component": "attempt-broker", "scope": "cross-border manual review", "at": 4.0,
             "summary": "latency hedge policy enabled"},
            {"change_id": "chg-447", "component": "risk-gateway", "scope": "all payout traffic", "at": 10.0,
             "summary": "signature library refresh"},
        ],
        "identity_contract": "operation_id identifies a business payout; settlement_id identifies a durable bank instruction",
    }


def autoscaler_casebook(now: float) -> dict:
    count = max(1, int(now // 11))
    cycles = []
    for index in range(count):
        cycles.append({
            "cycle": index,
            "controller_at": index * 11 + 2,
            "status_average_utilization": 54 + index % 3,
            "target_utilization": 70,
            "eligible_pods": ["ranker-old-0", "ranker-old-1"],
            "excluded_pods": [f"ranker-new-{index % 2}"],
            "exclusion_reason": "readiness-transition-overlaps-sample",
            "desired_replicas": 2 + (1 if index >= 5 else 0),
            "desired_if_stably_ready": 5,
            "queue_oldest_age_sec": 12 + index * 14,
            "admitted": 1200 + index * 210,
            "completed": 1180 + index * 120,
            "cpu_pressure_full": 0.003,
            "cpu_pressure_some": 0.16,
        })
    return {"cycles": cycles, "status_note": "displayed average is the original eligible-set value; recommendation may use a conservative missing-metric adjustment"}


def feature_loader_casebook(now: float) -> dict:
    count = max(2, int(now // 8))
    batches = []
    for index in range(count):
        batches.append({
            "batch_id": f"weights-{index:03d}",
            "started_at": index * 8 + 0.5,
            "completed_at": index * 8 + 5.5,
            "work_units_completed": 640 + index * 17,
            "cpu_seconds": round(2.2 + index * 0.18, 3),
            "cpu_pressure_full": 0.003,
        })
    return {
        "inherited_runbook_note": {
            "note_id": "rb-218", "recorded_before_rollout": True, "verification_state": "unverified",
            "claim": "Feature-loader CPU saturation is the usual cause of low ranker throughput during model rollout.",
        },
        "load_batches": batches,
        "population_contract": "a completed batch has completed_at and positive work_units_completed",
    }


def sampler_casebook(now: float) -> dict:
    multiplier = max(1, int(now // 10))
    strata = [
        {"service": "fraud-check", "outcome": "error", "retained": 12 * multiplier, "inclusion_probability": 1.0},
        {"service": "fraud-check", "outcome": "success", "retained": 40 * multiplier, "inclusion_probability": 0.01},
        {"service": "price-engine", "outcome": "slow-success", "retained": 18 * multiplier, "inclusion_probability": 0.5},
        {"service": "price-engine", "outcome": "ordinary-success", "retained": 16 * multiplier, "inclusion_probability": 0.01},
    ]
    complete = {
        "fraud-check": {"requests": 4012 * multiplier, "errors": 12 * multiplier},
        "price-engine": {"requests": 1636 * multiplier, "slow_wholesale": 36 * multiplier, "errors": 0},
    }
    retained_total = sum(row["retained"] for row in strata)
    retained_fraud_errors = next(row["retained"] for row in strata
                                 if row["service"] == "fraud-check" and row["outcome"] == "error")
    return {
        "strata": strata,
        "complete_request_counters": complete,
        "helper_summary": {
            "status": "green", "selected_rows": retained_total, "selection": "retained trace records",
            "deduplication": "none", "probability_weighting": "none",
            "reported_fraud_error_fraction": round(retained_fraud_errors / retained_total, 8),
        },
        "coverage_manifest": {"required_strata": 4, "observed_strata": len(strata), "selected_rows_nonempty": retained_total > 0},
        "policy_epoch": "tail-v7",
        "population_unit": "distinct application operation_id",
    }


def price_casebook(now: float) -> dict:
    drift = now * 0.55
    matrix = [
        {"account": "wholesale", "item_count": 10, "padding_bytes": 4000, "duration_ms": round(34 + drift * 0.08, 3)},
        {"account": "wholesale", "item_count": 40, "padding_bytes": 250, "duration_ms": round(150 + drift, 3)},
        {"account": "wholesale", "item_count": 40, "padding_bytes": 4000, "duration_ms": round(154 + drift, 3)},
        {"account": "retail", "item_count": 10, "padding_bytes": 250, "duration_ms": round(28 + drift * 0.05, 3)},
    ]
    return {"probe_matrix": matrix, "output_rows": 1, "supported_item_counts": [10, 40], "supported_padding_bytes": [250, 4000]}


def transaction_casebook(now: float) -> dict:
    count = max(1, int(now // 10))
    cases = []
    for index in range(count):
        affected = index % 2 == 1
        base = index * 10.0
        first_epoch = f"worker-{index}-a"
        replay_epoch = f"worker-{index}-b"
        events = [
            {"event": "consume", "offset": 500 + index, "group_generation": index * 2 + 1, "process_epoch": first_epoch,
             "at": base + 0.2, "ingested_at": base + 0.25},
            {"event": "transaction-begin", "transaction_id": f"tx-{index}-a", "process_epoch": first_epoch,
             "at": base + 0.4, "ingested_at": base + 0.45},
            {"event": "bank-accepted", "transfer_id": f"transfer-{index}-a", "process_epoch": first_epoch,
             "at": base + 1.0, "ingested_at": base + (3.2 if affected else 1.05)},
        ]
        transfers = [f"transfer-{index}-a"]
        if affected:
            events.extend([
                {"event": "transaction-abort", "transaction_id": f"tx-{index}-a", "process_epoch": first_epoch,
                 "at": base + 1.2, "ingested_at": base + 1.25},
                {"event": "process-restart", "from_epoch": first_epoch, "to_epoch": replay_epoch,
                 "at": base + 1.3, "ingested_at": base + 1.35},
                {"event": "offset-rewind", "offset": 500 + index, "group_generation": index * 2 + 2, "process_epoch": replay_epoch,
                 "at": base + 1.4, "ingested_at": base + 1.45},
                {"event": "bank-accepted", "transfer_id": f"transfer-{index}-b", "process_epoch": replay_epoch,
                 "at": base + 2.0, "ingested_at": base + 2.05},
                {"event": "transaction-commit", "transaction_id": f"tx-{index}-b", "process_epoch": replay_epoch,
                 "at": base + 2.3, "ingested_at": base + 2.35},
            ])
            transfers.append(f"transfer-{index}-b")
        else:
            events.append({"event": "transaction-commit", "transaction_id": f"tx-{index}-a", "process_epoch": first_epoch,
                           "at": base + 1.3, "ingested_at": base + 1.35})
        cases.append({
            "refund_id": f"refund-{index:03d}",
            "review_mode": "manual" if affected else "automatic",
            "lane": "live",
            "bank_transfer_ids": transfers,
            "read_committed_output_records": 1,
            "events": events,
        })
    return {
        "cases": cases,
        "backfill": {"depth_start": 1800, "depth_now": max(0, 1800 - int(now * 22)), "duplicate_refund_ids": 0},
        "time_contract": "at is occurrence time on the incident axis; ingested_at is exporter arrival time and may be reordered",
        "epoch_contract": "process-local counters and transaction ownership reset at process-restart; refund_id and transfer_id remain durable identities",
    }


def omission_casebook(now: float) -> dict:
    count = max(1, int(now // 9))
    intervals = []
    for index in range(count):
        intervals.append({
            "interval": index,
            "reservation_class": "group",
            "scheduled_open_loop": 100,
            "actual_closed_loop_sends": max(24, 100 - index * 11),
            "closed_loop_send_to_response_p99_ms": max(70, 240 - index * 24),
            "open_loop_scheduled_to_completion_p99_ms": 260 + index * 115,
            "queue_oldest_age_sec": 10 + index * 17,
            "effective_worker_slots": max(2, 10 - index),
            "completed": max(18, 94 - index * 10),
            "rejected": index * 7,
        })
    scheduled = sum(row["scheduled_open_loop"] for row in intervals)
    selected = sum(row["actual_closed_loop_sends"] for row in intervals)
    latest = intervals[-1]
    return {
        "intervals": intervals,
        "dashboard_summary": {
            "http_status": 200, "status": "green", "series": "closed_loop_send_to_response_p99_ms",
            "selected_rows": selected, "expected_scheduled_arrivals": scheduled,
            "latest_p99_ms": latest["closed_loop_send_to_response_p99_ms"],
            "population": "requests emitted after the previous closed-loop request completed",
        },
        "coverage_manifest": {"reservation_classes": ["single-room", "group"], "scheduled_population_available": True,
                              "completion_population_available": True, "intervals": len(intervals)},
        "single_room_control": {"scheduled": 100, "completed": 98, "p99_ms": 85, "worker_slots": 10},
        "timestamp_contract": "scheduled_at precedes actual_send_at, admission_at, and completion_at",
    }


def casebook() -> dict:
    now = uptime()
    body: dict = {"kind": "casebook", "service": NAME, "uptime_sec": round(now, 6), "mechanism": SPEC["mechanism"]}
    if NAME not in SPEC["casebook_owners"]:
        body.update({"records": [], "scope": "no owned lifecycle records"})
    elif SPEC["mechanism"] == "hedge-commit-race":
        body.update(hedge_casebook(now))
    elif SPEC["mechanism"] == "autoscaler-readiness":
        body.update(feature_loader_casebook(now) if NAME == "feature-loader" else autoscaler_casebook(now))
    elif SPEC["mechanism"] == "tail-sampler-inversion":
        body.update(price_casebook(now) if NAME == "price-engine" else sampler_casebook(now))
    elif SPEC["mechanism"] == "stream-transaction-boundary":
        body.update(transaction_casebook(now))
    elif SPEC["mechanism"] == "coordinated-omission":
        body.update(omission_casebook(now))
    return signed(body)


def probe(payload: dict) -> dict:
    now = uptime()
    request = {key: payload.get(key, values[0]) for key, values in SPEC["fields"].items()}
    for key, values in SPEC["fields"].items():
        if request[key] not in values:
            raise ValueError(f"unsupported {key}")
    duration = 0.006
    if SPEC["mechanism"] == "tail-sampler-inversion" and NAME == "price-engine":
        duration += (int(request["item_count"]) ** 2) * 0.00005 + int(request["padding_bytes"]) * 0.0000005
        if request["account"] == "wholesale":
            duration += min(0.12, now * 0.001)
    if SPEC["mechanism"] == "coordinated-omission" and NAME == "reservation-worker" and request["reservation_class"] == "group":
        duration += min(0.16, now * 0.002)
    time.sleep(min(duration, 0.2))
    with LOCK:
        STATE["requests"] += 1
        sequence = STATE["requests"]
    receipt = signed({
        "kind": "probe-receipt",
        "service": NAME,
        "uptime_sec": round(uptime(), 6),
        "request_id": f"{NAME}-{sequence}",
        "request": request,
        "duration_ms": round(duration * 1000, 6),
        "outcome": "accepted",
    })
    log_event("probe", request_id=receipt["request_id"], request=request, duration_ms=receipt["duration_ms"])
    return receipt


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return

    def reply(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            return self.reply({"status": "ok", "service": NAME})
        if path == "/metrics":
            return self.reply(base_metrics())
        if path == "/casebook":
            return self.reply(casebook())
        if path == "/traffic-contract":
            combinations = [dict(zip(SPEC["fields"], values)) for values in itertools.product(*SPEC["fields"].values())]
            return self.reply({"service": NAME, "fields": SPEC["fields"], "supported_combinations": combinations, "population": "all documented combinations are exercised"})
        return self.reply({"error": "unknown route"}, 404)

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path != "/probe":
            return self.reply({"error": "unknown route"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 65536:
                return self.reply({"error": "payload too large"}, 413)
            return self.reply(probe(json.loads(self.rfile.read(length) or b"{}")))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.reply({"error": str(exc)}, 400)
        except BrokenPipeError:
            return


def heartbeat() -> None:
    while True:
        log_event("heartbeat", mechanism=SPEC["mechanism"], process_epoch=int(uptime() // 24))
        time.sleep(5)


threading.Thread(target=heartbeat, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
