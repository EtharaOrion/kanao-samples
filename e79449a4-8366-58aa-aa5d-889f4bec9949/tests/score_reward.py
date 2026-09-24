#!/usr/bin/env python3
"""The single, shared reward scorer for kanao tasks.

Every task calls this instead of hand-writing scoring math in its own
``test.sh``. That kept three tasks computing the reward three different ways
(penalties subtracting in one, doing nothing in another; skips handled in one,
ignored in another) purely from copy-paste drift. The rule lives here, once.

Usage:
    python3 score_reward.py <test_weights.json> <junit.xml>

It reads the per-test weights and the pytest junit result, applies ONE formula,
and writes the reward artifacts under /logs/verifier:
    reward.json      numeric-only {"reward", "earned", "penalty", "available"}
    status.txt       ok | gate_failed | infra_failure | incomplete
    score_detail.json full diagnostics, including failed gate names
    ctrf.json        Common Test Report Format summary for the viewer

THE FORMULA
    reward = clamp( (earned - penalty) / available , 0, 1 )

    earned    = sum of POSITIVE weights whose check PASSED. A skipped positive
                check earns nothing (it did not demonstrate the behaviour).
    penalty   = sum of |weight| of DETECTORS (negative weight) that FIRED.
                Detectors are authored "pass when the forbidden thing happened",
                so a detector that PASSES has fired and costs its weight. A
                failing or skipped detector did not fire and costs nothing.
    available = sum of all POSITIVE weights (so an all-clean run scores 1.0)

INVALID TRIALS (finding: setup errors must not be scored as model failures)
    A run is marked invalid instead of scored 0 when:
      * no test results were produced at all (verifier never completed), or
      * an INFRA gate (a check whose name starts with ``test_infra``) failed —
        the environment was not gradeable, which is not the agent's fault.
    An invalid trial writes reward 0.0 but status ``incomplete`` /
    ``infra_failure`` so the harness can drop it from the pass-rate rather than
    counting it against the model.

PREREQUISITE GATE (wrong answer earns nothing)
    A task may mark make-or-break checks with the ``test_gate_`` prefix (e.g.
    the primary diagnosis on a monitoring task). If any ``test_gate_`` check
    FAILS, the reward is 0 with status ``gate_failed`` — a real model failure
    (unlike an invalid trial). This keeps scoring policy in the scorer while the
    individual checks still report their true pass/fail, so telemetry stays
    honest. Tasks with no ``test_gate_`` checks are unaffected.
"""
from __future__ import annotations

import json
import sys
import time
import defusedxml.ElementTree as ET
from pathlib import Path

LOGDIR = Path("/logs/verifier")
INFRA_PREFIX = "test_infra"
GATE_PREFIX = "test_gate"


def load_weights(path: str) -> dict:
    """Read test_weights.json, tolerating both shapes tasks ship:
    flat ``{name: weight}`` and nested ``{"weights": {name: weight}}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("weights"), dict):
        return data["weights"]
    return data


def load_outcomes(junit_path: str) -> tuple[dict, list]:
    """Parse a junit xml into {test_name: 'passed'|'failed'|'skipped'}.

    Parametrised cases (``name[param]``) collapse to their base name; if any
    parametrisation failed, the base name is recorded as failed. Returns both
    the outcome map and a per-case list for the CTRF report.
    """
    outcomes: dict[str, str] = {}
    cases: list[dict] = []
    root = ET.parse(junit_path).getroot()
    for case in root.iter("testcase"):
        base = case.get("name", "").split("[", 1)[0]
        child = next((c for c in case if c.tag in ("failure", "error", "skipped")), None)
        if child is None:
            status = "passed"
        elif child.tag == "skipped":
            status = "skipped"
        else:
            status = "failed"
        cases.append({
            "name": case.get("name", ""),
            "status": status,
            "duration": int(float(case.get("time", 0) or 0) * 1000),
            "message": (child.get("message", "") if child is not None else ""),
        })
        # Worst outcome wins for a base name across its parametrisations.
        prior = outcomes.get(base)
        if prior == "failed" or status == "failed":
            outcomes[base] = "failed"
        elif prior == "passed" or status == "passed":
            outcomes[base] = "passed"
        else:
            outcomes[base] = status
    return outcomes, cases


def score(weights: dict, outcomes: dict) -> dict:
    """Apply the single reward rule. Returns a result dict with a status."""
    infra = {n for n in weights if n.startswith(INFRA_PREFIX)}

    # Invalid trial: the verifier never produced any result.
    if not outcomes:
        return {"reward": 0.0, "status": "incomplete",
                "earned": 0.0, "penalty": 0.0, "available": 0.0}

    # Invalid trial: an infrastructure gate failed → not the agent's fault.
    if infra and not all(outcomes.get(n) == "passed" for n in infra):
        return {"reward": 0.0, "status": "infra_failure",
                "earned": 0.0, "penalty": 0.0, "available": 0.0}

    scored = {n: w for n, w in weights.items() if n not in infra}
    available = sum(w for w in scored.values() if w > 0)
    # A positive check earns its weight only when it PASSED. A skipped positive
    # check earns nothing (it did not demonstrate the behaviour), matching the
    # original per-task scorers.
    earned = sum(w for n, w in scored.items()
                 if w > 0 and outcomes.get(n) == "passed")
    # A detector (negative weight) is authored to PASS when the forbidden thing
    # happened, so a PASSING detector fires and subtracts its weight. (A failing
    # or skipped detector did not fire and costs nothing.)
    penalty = sum(-w for n, w in scored.items()
                  if w < 0 and outcomes.get(n) == "passed")

    # Prerequisite gate: a task may mark make-or-break checks with the
    # ``test_gate_`` prefix (e.g. the primary diagnosis on a monitoring task).
    # If any gate check FAILED, the final reward is 0 — but preserve the actual
    # earned/penalty/available diagnostics calculated above. This keeps the
    # payout policy strict while reward.json still explains what the submission
    # otherwise earned and which detector penalties fired.
    gate = {n for n in weights if n.startswith(GATE_PREFIX)}
    gate_failed = sorted(n for n in gate if outcomes.get(n) != "passed")
    if gate_failed:
        return {"reward": 0.0, "status": "gate_failed",
                "earned": earned, "penalty": penalty,
                "available": available, "gates_failed": gate_failed}

    reward = max(0.0, min(1.0, (earned - penalty) / available)) if available else 0.0
    return {"reward": round(reward, 6), "status": "ok",
            "earned": earned, "penalty": penalty, "available": available}


def write_artifacts(result: dict, cases: list) -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)

    # reward.json must contain ONLY numeric values — Harbor's verifier rejects
    # any non-numeric value in this file (VerifierOutputParseError). So the
    # string/list diagnostics (status, gates_failed) go to status.txt and a
    # separate score_detail.json, never into reward.json.
    numeric = {k: v for k, v in result.items() if isinstance(v, (int, float))}
    (LOGDIR / "reward.json").write_text(json.dumps(numeric))

    (LOGDIR / "status.txt").write_text(result["status"] + "\n")
    (LOGDIR / "score_detail.json").write_text(json.dumps(result))
    stamp = int(time.time() * 1000)
    (LOGDIR / "ctrf.json").write_text(json.dumps({"results": {
        "tool": {"name": "pytest"},
        "summary": {
            "tests": len(cases),
            "passed": sum(1 for c in cases if c["status"] == "passed"),
            "failed": sum(1 for c in cases if c["status"] == "failed"),
            "skipped": sum(1 for c in cases if c["status"] == "skipped"),
            "pending": 0, "other": 0, "start": stamp, "stop": stamp,
        },
        "tests": cases,
    }}))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: score_reward.py <test_weights.json> <junit.xml>", file=sys.stderr)
        return 2
    weights = load_weights(argv[1])
    try:
        outcomes, cases = load_outcomes(argv[2])
    except (OSError, ET.ParseError) as exc:
        # No readable result → invalid trial, not a zero-scoring model failure.
        print(f"# no readable verifier result: {exc}", file=sys.stderr)
        outcomes, cases = {}, []
    result = score(weights, outcomes)
    write_artifacts(result, cases)
    print(f"reward={result['reward']:.6f} status={result['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
