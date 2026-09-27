#!/usr/bin/env python3
"""In-verifier rubric judge: grade one trial's rubric from inside harbor's verifier.

Runs where `tests/test.sh` runs — the separate verifier container harbor builds
from tests/ — so the rubric channel is scored by stock harbor in the same
`harbor run` as the deterministic tests, with no harness on the host. The judge
is the harness's own council package, vendored beside this file by
scripts/sync_judge.py (`tests/grader/`), so a trial graded here and one graded
on the host by grade.py go through the same prompts, verdict parser and
aggregation.

Evidence, assembled into the trial layout judge/evidence.py reads:
    --agent-dir /logs/agent           transcript: trajectory.json, oracle.txt, …
                                      (reaches a separate verifier only when
                                      task.toml collects /logs/agent as an artifact)
    --workspace /app [...]            graded files, re-materialised by harbor at
                                      their original paths
    --verifier-dir /logs/verifier     the deterministic tests' reward + pytest output
    --task-description /tests/instruction.md

Transport is HTTP only (stdlib; no CLI, no SDK), configured by the task's
[verifier.env]:
    JUDGE_WIRE              openai | anthropic            (default openai)
    JUDGE_MODEL             model id                      (default per wire, warned)
    OPENAI_API_KEY / ANTHROPIC_API_KEY                    the wire's credential
    OPENAI_BASE_URL / ANTHROPIC_BASE_URL                  optional endpoint override
    JUDGE_COUNCIL_MEMBERS   full roster override, e.g. "primary=openai/gpt-5.6-sol"

Exit codes: 0 graded (breakdown written); 4 preflight — unknown wire, no
credential, no model — nothing written, the rubric stays unscored; 1 the judge
ran but produced no verdict — rubric_judge_failed.txt is written beside the
output and no breakdown is left behind.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _base in (HERE, HERE.parent):
    if (_base / "judge" / "__init__.py").is_file():
        sys.path.insert(0, str(_base))
        break

from judge import council  # noqa: E402
from judge.council import CouncilMember, council_members, credential_error, grade_council  # noqa: E402
from judge.evidence import (  # noqa: E402
    build_transcript,
    build_verifier_output,
    build_workspace_state,
    tests_reward,
    tests_status,
)
from judge.pricing import judge_cost_usd  # noqa: E402

WIRES = ("openai", "anthropic")
DEFAULT_WIRE = "openai"
DEFAULT_MODELS = {"openai": "gpt-5.6-sol", "anthropic": "claude-sonnet-5"}
BASE_URL_ENV = {
    "openai": ("OPENAI_BASE_URL", "JUDGE_OPENAI_BASE_URL"),
    "anthropic": ("ANTHROPIC_BASE_URL", "JUDGE_ANTHROPIC_BASE_URL"),
}
PYTEST_LOG_CANDIDATES = ("test-stdout.txt", "pytest.log", "evidence/pytest.txt")
VERIFIER_FILES = ("reward.txt", "reward.json", "status.txt")
AGENT_DIR_IGNORE = ("sessions",)
MARKER = "rubric_judge_failed.txt"
PRODUCER = "in_verifier"
EXIT_GRADED = 0
EXIT_UNSCORED = 1
EXIT_PREFLIGHT = 4


def _has_credential(wire: str) -> bool:
    """Whether a usable key for `wire` is already sitting in the environment.

    Checked with the same env names council.py itself reads (including the
    judge-scoped overrides), so this agrees with what preflight() will find a
    moment later.
    """
    if wire == "openai":
        return bool(os.environ.get("JUDGE_OPENAI_API_KEY", "").strip()
                    or os.environ.get("OPENAI_API_KEY", "").strip())
    if wire == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    return False


def _autodetect_wire() -> str:
    """The wire to use when JUDGE_WIRE is not set explicitly.

    A client thinks in terms of "I have an OpenAI key" or "I have an
    Anthropic key", not "I have a key, and I must also name which wire it is
    for" — so when exactly one of the two credentials is present, that is the
    wire. Only when both or neither are set does this fall back to
    DEFAULT_WIRE, where an explicit JUDGE_WIRE is the way to disambiguate.
    """
    if _has_credential("anthropic") and not _has_credential("openai"):
        return "anthropic"
    return DEFAULT_WIRE


def export_base_urls() -> None:
    """Map the task-declared SDK base URLs onto the judge-scoped names council.py reads.

    council.py ignores OPENAI_BASE_URL / ANTHROPIC_BASE_URL on purpose (a host
    shell may export them for other tools); inside the verifier the environment
    is exactly what task.toml declared, so the plain names are trusted here.
    """
    for plain, scoped in BASE_URL_ENV.values():
        value = os.environ.get(plain, "").strip()
        if value and not os.environ.get(scoped, "").strip():
            os.environ[scoped] = value


def load_rubric(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("criteria") if isinstance(raw.get("criteria"), list) else raw.get("rubric")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path} must be a non-empty list of criteria (or {{'criteria': [...]}})")
    criteria: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            criteria.append({"criterion": str(item)})
            continue
        entry = dict(item)
        weight = entry.get("weight", entry.get("score"))
        if entry.get("is_positive") is False and isinstance(weight, (int, float)) and weight > 0:
            entry["weight"] = -float(weight)
        criteria.append(entry)
    return criteria


def resolve_roster(wire: str, model: str) -> tuple[list[CouncilMember], str]:
    if os.environ.get("JUDGE_COUNCIL_MEMBERS", "").strip():
        members = council_members()
        return members, "JUDGE_COUNCIL_MEMBERS=" + ",".join(f"{m.family}={m.model}" for m in members)
    return [CouncilMember("primary", f"{wire}/{model}")], f"{wire}/{model}"


def preflight(wire: str, model: str, members: list[CouncilMember], roster_from_env: bool) -> str | None:
    if not roster_from_env and wire not in WIRES:
        return f"JUDGE_WIRE={wire!r} is not one of {list(WIRES)}; the rubric channel is left unscored"
    if not roster_from_env and not model:
        return "no judge model: pass --model or set JUDGE_MODEL; the rubric channel is left unscored"
    for member in members:
        err = credential_error(member.model)
        if err:
            return f"seat {member.family} ({member.model}): {err}; the rubric channel is left unscored"
    return None


def _relative_under_artifacts(path: Path) -> Path:
    parts = [p for p in path.parts if p not in ("/", "")]
    return Path(*parts) if parts else Path(path.name)


def stage_trial(tmp: Path, agent_dir: Path | None, workspaces: list[Path],
                verifier_dir: Path | None, pytest_log: Path | None) -> tuple[Path, dict]:
    """Copy the verifier's evidence into a harbor-shaped trial dir; returns (trial, sources)."""
    trial = tmp / "trial"
    for sub in ("agent", "artifacts", "verifier"):
        (trial / sub).mkdir(parents=True)
    sources: dict = {"agent_dir": None, "workspaces": [], "verifier_dir": None, "pytest_log": None}
    if agent_dir is not None and agent_dir.is_dir():
        shutil.copytree(agent_dir, trial / "agent", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*AGENT_DIR_IGNORE))
        sources["agent_dir"] = str(agent_dir)
    for ws in workspaces:
        target = trial / "artifacts" / _relative_under_artifacts(ws)
        if ws.is_dir():
            shutil.copytree(ws, target, dirs_exist_ok=True)
        elif ws.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ws, target)
        else:
            continue
        sources["workspaces"].append(str(ws))
    if verifier_dir is not None and verifier_dir.is_dir():
        sources["verifier_dir"] = str(verifier_dir)
        for name in VERIFIER_FILES:
            if (verifier_dir / name).is_file():
                shutil.copy2(verifier_dir / name, trial / "verifier" / name)
        log = pytest_log if pytest_log and pytest_log.is_file() else next(
            (verifier_dir / c for c in PYTEST_LOG_CANDIDATES if (verifier_dir / c).is_file()), None)
        if log is not None:
            shutil.copy2(log, trial / "verifier" / "pytest.log")
            sources["pytest_log"] = str(log)
    return trial, sources


def combined_score(tests: float | None, rubric: float, weight_tests: float) -> float | None:
    if tests is None:
        return None
    return round(max(0.0, min(1.0, weight_tests * tests + (1.0 - weight_tests) * rubric)), 6)


def token_lines(scores: dict) -> list[dict]:
    lines: list[dict] = []
    for family, entry in scores["judge_council"]["per_member"].items():
        model = entry["model"]
        lines.append({
            "model_name": model,
            "judge_wire": _wire_of(scores, family),
            "judge_input_tokens": entry["input_tokens"],
            "judge_output_tokens": entry["output_tokens"],
            "judge_input_cache_tokens": entry["cache_read_tokens"],
            "judge_output_cache_tokens": entry["cache_write_tokens"],
            "judge_cost_usd": judge_cost_usd(
                model, entry["input_tokens"], entry["output_tokens"],
                entry["cache_read_tokens"], entry["cache_write_tokens"]),
            "seat": family,
            "ok": entry["ok"],
        })
    return lines


def _wire_of(scores: dict, family: str) -> str:
    return scores["judge_council"].get("wires", {}).get(family, "")


def _write_json(path: Path, doc: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _fail(marker: Path, output: Path, reason: str, detail: dict | None = None) -> int:
    marker.parent.mkdir(parents=True, exist_ok=True)
    lines = [reason] + [f"{k}: {v}" for k, v in (detail or {}).items()]
    marker.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if output.is_file():
        output.unlink()
    print(f"ERROR: {reason}; rubric channel is UNSCORED. See {marker}", file=sys.stderr)
    return EXIT_UNSCORED


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rubric", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path, help="rubric_breakdown.json to write")
    ap.add_argument("--token-output", type=Path, help="judge token usage, one line per seat")
    ap.add_argument("--task-description", type=Path, help="the task's instruction.md")
    ap.add_argument("--agent-dir", type=Path, default=Path("/logs/agent"))
    ap.add_argument("--workspace", type=Path, nargs="*", default=[],
                    help="files or directories to show the judge as the workspace state")
    ap.add_argument("--verifier-dir", type=Path, default=Path("/logs/verifier"))
    ap.add_argument("--pytest-log", type=Path,
                    help="pytest output to show the judge (default: found under --verifier-dir)")
    ap.add_argument("--wire", choices=WIRES, help="judge transport; defaults to JUDGE_WIRE, else openai")
    ap.add_argument("--model", help="judge model; defaults to JUDGE_MODEL, else the wire's default")
    ap.add_argument("--weight-tests", type=float, default=0.5,
                    help="the tests' share of combined_score (default: 0.5)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the roster and evidence sizes; make no call, write nothing")
    args = ap.parse_args(argv)

    export_base_urls()
    wire = (args.wire or os.environ.get("JUDGE_WIRE", "").strip() or _autodetect_wire()).lower()
    model = (args.model or os.environ.get("JUDGE_MODEL", "").strip()
             or os.environ.get(council.MODEL_PIN_ENV, "").strip())
    roster_from_env = bool(os.environ.get("JUDGE_COUNCIL_MEMBERS", "").strip())
    if not model and not roster_from_env:
        model = DEFAULT_MODELS.get(wire, "")
        if model:
            print(f"[rubric-judge] WARNING: JUDGE_MODEL is not set; falling back to {model!r} "
                  f"for the {wire} wire. Pin JUDGE_MODEL to keep the grader constant across "
                  "trials.", file=sys.stderr)
    try:
        members, roster = resolve_roster(wire, model)
    except ValueError as err:
        print(f"[rubric-judge] {err}; the rubric channel is left unscored", file=sys.stderr)
        return EXIT_PREFLIGHT
    problem = preflight(wire, model, members, roster_from_env)
    if problem:
        print(f"[rubric-judge] {problem}", file=sys.stderr)
        return EXIT_PREFLIGHT

    output = args.output
    marker = output.parent / MARKER
    try:
        rubric = load_rubric(args.rubric)
    except (OSError, ValueError) as err:
        return _fail(marker, output, f"rubric unusable: {err}")
    task_description = "(instruction.md not found)"
    if args.task_description and args.task_description.is_file():
        task_description = args.task_description.read_text(encoding="utf-8", errors="replace")

    with tempfile.TemporaryDirectory(prefix="rubric-judge-") as tmp:
        trial, sources = stage_trial(Path(tmp), args.agent_dir, args.workspace,
                                     args.verifier_dir, args.pytest_log)
        transcript = build_transcript(trial)
        workspace = build_workspace_state(trial)
        verifier = build_verifier_output(trial)
        reward = tests_reward(trial)
        status = tests_status(trial)

        summary = (f"{len(rubric)} criteria · roster {roster} · transport {council.transport_summary()} · "
                   f"evidence transcript {len(transcript):,} / workspace {len(workspace):,} / "
                   f"verifier {len(verifier):,} chars · tests {reward} ({status})")
        if args.dry_run:
            print(f"[rubric-judge] dry run: {summary}")
            print(f"[rubric-judge] sources: {json.dumps(sources)}")
            print("--- transcript head ---")
            print(transcript[:1200])
            return EXIT_GRADED
        print(f"[rubric-judge] grading: {summary}")
        scores = grade_council(rubric, task_description, transcript, workspace, verifier, members=members)

    scores["judge_council"]["wires"] = {m.family: council._split_provider(m.model)[0] for m in members}
    if not scores["judge_council"]["surviving"]:
        errors = {f"seat {fam}": entry.get("error", "no verdict")
                  for fam, entry in scores["judge_council"]["per_member"].items()}
        return _fail(marker, output, "judge produced no verdicts", {
            "roster": roster, "criteria": len(rubric), **errors,
        })

    rubric_score = scores["overall_score"]
    doc = {
        **scores,
        "score": rubric_score,
        "rubric_score": rubric_score,
        "tests_score": reward,
        "tests_status": status,
        "tests_weight": args.weight_tests,
        "combined_score": combined_score(reward, rubric_score, args.weight_tests),
        "roster": roster,
        "producer": PRODUCER,
        "evidence": {
            "transcript_chars": len(transcript),
            "workspace_chars": len(workspace),
            "verifier_chars": len(verifier),
            **sources,
        },
    }
    if marker.is_file():
        marker.unlink()
    _write_json(output, doc)
    if args.token_output:
        _write_json(args.token_output, token_lines(scores))

    for c in scores["criteria"]:
        mark = "PASS" if c["passed"] else ("HUMAN" if c["resolved_by"] == "human_eval" else "FAIL")
        print(f"  {mark:5} {c['number']:>4} w={c['weight']:+.0f} votes={c['votes']:<12} by={c['resolved_by']}")
    print(f"[rubric-judge] rubric {rubric_score:.4f} ({scores['criteria_passed']}/{scores['criteria_total']} "
          f"passed, {scores['criteria_abstained']} abstained) · tests {reward} · "
          f"combined {doc['combined_score']} → {output}")
    return EXIT_GRADED


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — a crashed judge must still leave the marker, never a partial breakdown
        traceback.print_exc()
        out = Path(next((a for i, a in enumerate(sys.argv) if i and sys.argv[i - 1] == "--output"),
                        "/logs/verifier/rubric_breakdown.json"))
        raise SystemExit(_fail(out.parent / MARKER, out, f"judge crashed: {exc}"))
