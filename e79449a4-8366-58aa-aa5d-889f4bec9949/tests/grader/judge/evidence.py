"""Build judge evidence from a harbor trial directory.

Harbor writes one directory per trial:

    <trial>/
      agent/trajectory.json     ATIF transcript (every agent)
      agent/claude-code.txt     stream-json (claude-code agent only, richer)
      artifacts/                collected environment paths, if task.toml asks
      verifier/test-stdout.txt  deterministic grader output
      verifier/reward.txt       deterministic tests reward (1 / 0), or
      verifier/reward.json      {"reward": …, …} from the shared scorer
      result.json               phase timings, usage, exception info

This module turns that into the four evidence blocks the judge prompt expects.
Everything is best-effort: a missing block is reported to the judge as an
explicit absence rather than silently omitted, because "no evidence" and "the
evidence shows it didn't happen" must not look alike to a grader.
"""

from __future__ import annotations

import json
from pathlib import Path

# Per-block caps. Generous enough for these tasks, bounded so a runaway log
# cannot blow the judge's context window. Truncation is always marked so the
# judge can set TRUNCATION_AFFECTED honestly.
MAX_TRANSCRIPT_CHARS = 300_000
MAX_WORKSPACE_CHARS = 120_000
MAX_VERIFIER_CHARS = 20_000
MAX_TOOL_RESULT_CHARS = 4_000

TRUNCATION_MARKER = "\n... [truncated]\n"

# Every file task.toml collected is a deliverable the judge should see, except
# service log subtrees (container noise, not agent output) and the manifest
# harbor writes about the collection itself.
WORKSPACE_EXCLUDE_DIRS = ("logs",)
WORKSPACE_SKIP_FILES = ("manifest.json",)
# Bulky JSON arrays (signed observation rows: dozens of ~900-char records) are
# elided to their first and last element so a deliverable's prose fields
# survive the per-block cap instead of being pushed past it by raw telemetry.
# Both limits must be exceeded: a compact array — six casebook intervals, a
# handful of readings — is evidence the judge can check a cited value against,
# and stays whole.
MAX_ARRAY_ITEMS = 3
MAX_ARRAY_CHARS = 4_000


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_MARKER


def _tool_summary(name: str, payload: dict) -> str:
    """One-line rendering of a tool call, keeping the arguments that matter."""
    if name == "Bash":
        cmd = payload.get("command", "")
        return f"[Tool Call: Bash] {cmd}"
    if name in ("Edit", "Write"):
        path = payload.get("file_path", "")
        old = payload.get("old_string")
        new = payload.get("new_string", payload.get("content", ""))
        parts = [f"[Tool Call: {name}] {path}"]
        if old:
            parts.append(f"  - {old}")
        if new:
            parts.append(f"  + {new}")
        return "\n".join(parts)
    if name == "Read":
        return f"[Tool Call: Read] {payload.get('file_path', '')}"
    return f"[Tool Call: {name}] {json.dumps(payload)[:600]}"


def transcript_from_claude_code(path: Path) -> str:
    """Render claude-code's stream-json log into the judge transcript format."""
    lines: list[str] = []
    final_text = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            evt = json.loads(raw)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        msg = evt.get("message", {}) or {}
        if etype == "assistant":
            for block in msg.get("content", []) or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    final_text = block["text"]
                    lines.append(f"[ASSISTANT] {block['text']}")
                elif block.get("type") == "tool_use":
                    lines.append(_tool_summary(block.get("name", "?"), block.get("input", {})))
        elif etype == "user":
            for block in msg.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(
                            c.get("text", "") for c in content if isinstance(c, dict)
                        )
                    lines.append(
                        f"[Tool Result] {_clip(str(content), MAX_TOOL_RESULT_CHARS)}"
                    )
        elif etype == "result" and evt.get("result"):
            final_text = str(evt["result"])
    if final_text:
        lines.append(f"\n[FINAL ASSISTANT MESSAGE]\n{final_text}")
    return "\n".join(lines)


# Arguments a terminal tool sends beside the command itself. When a tool call
# carries nothing else, the command IS the call and is rendered bare.
_SHELL_SIDE_ARGS = frozenset({
    "timeout", "is_input", "reset", "description", "security_risk", "restart",
    "run_in_background", "duration",
})


def _shell_command_of(args: dict) -> str | None:
    """The shell command a tool call runs, or None when it is not a shell call.

    terminus-2 sends terminal keystrokes. OpenHands' terminal tool (and
    claude-code's Bash) send a `command` — but so does OpenHands' file
    editor, whose `command` is the sub-command name ("view", "str_replace")
    next to `path`, `old_str`, `new_str`. Rendering that one word would hide
    the edit from the judge, so `command` only counts as the whole call when
    no other substantive argument travels with it; otherwise the caller
    dumps the full argument set.
    """
    keystrokes = args.get("keystrokes")
    if keystrokes:
        return str(keystrokes)
    command = args.get("command")
    if not command:
        return None
    if set(args) - _SHELL_SIDE_ARGS - {"command"}:
        return None
    return str(command)


def transcript_from_atif(path: Path) -> str:
    """Render an ATIF trajectory.json. Used for agents other than claude-code.

    An ATIF step carries three separate fields, and all three are evidence:
    `message` (the agent's prose), `tool_calls` (what it actually ran), and
    `observation` (what came back). Rendering only `message` starves the judge —
    it sees the agent *narrating* an action with no command to verify, and
    correctly answers No under the "prose without a matching tool call" rule.
    That produced a false FAIL on a terminus-2 run that had in fact run the
    command. Emit all three.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    lines: list[str] = []
    steps = data.get("steps", []) or []
    last_agent_message = ""

    for step in steps:
        source = str(step.get("source", "?")).upper()
        message = step.get("message")
        # The first step is the harness system prompt + task description, which
        # the judge already has verbatim in <question>. Repeating it wastes
        # context and invites the judge to grade the prompt.
        if message and step.get("step_id") != 1:
            lines.append(f"[{source}] {message}")
            if source == "AGENT":
                last_agent_message = str(message)

        for call in step.get("tool_calls") or []:
            args = call.get("arguments") or {}
            name = call.get("function_name", "tool")
            cmd = _shell_command_of(args)
            if cmd:
                lines.append(f"[Tool Call: {name}] {cmd.rstrip()}")
            else:
                lines.append(f"[Tool Call: {name}] {json.dumps(args)[:600]}")

        for result in (step.get("observation") or {}).get("results") or []:
            content = result.get("content")
            if content:
                lines.append(f"[Tool Result] {_clip(str(content), MAX_TOOL_RESULT_CHARS)}")

    if last_agent_message:
        lines.append(f"\n[FINAL ASSISTANT MESSAGE]\n{last_agent_message}")
    return "\n".join(lines)


# Raw agent logs, in preference order, for agents that leave no structured
# transcript: harbor's oracle redirects solve.sh's stdout/stderr to oracle.txt,
# its codex agent tees `codex exec` to codex.txt. Any other *.txt in agent/ is
# taken largest-first as a last resort. exit-code.txt is harbor's own note, not
# agent output.
RAW_AGENT_LOGS = ("oracle.txt", "codex.txt")
RAW_AGENT_LOG_SKIP = ("exit-code.txt",)
MAX_FINAL_MESSAGE_CHARS = 4_000


def _raw_agent_log(agent_dir: Path) -> Path | None:
    """The raw agent log to fall back on, or None when agent/ holds none."""
    if not agent_dir.is_dir():
        return None
    for name in RAW_AGENT_LOGS:
        candidate = agent_dir / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    others = sorted(
        (p for p in agent_dir.glob("*.txt")
         if p.is_file() and p.stat().st_size > 0 and p.name not in RAW_AGENT_LOG_SKIP),
        key=lambda p: p.stat().st_size, reverse=True,
    )
    return others[0] if others else None


def transcript_from_raw_log(path: Path) -> str:
    """Render a raw agent log (no tool-call structure) for the judge.

    The judge prompt expects the conversation to end with a
    [FINAL ASSISTANT MESSAGE]; for a log that is one undifferentiated stream
    the tail stands in for it, so explanation criteria have something to read
    without the judge inventing a structure the log never had. The header says
    what this is, so action criteria are judged on what the output *states*
    rather than on tool calls that were never recorded.
    """
    body = path.read_text(encoding="utf-8", errors="replace").strip()
    if not body:
        return ""
    tail = body if len(body) <= MAX_FINAL_MESSAGE_CHARS else body[-MAX_FINAL_MESSAGE_CHARS:]
    return (
        f"[AGENT LOG: {path.name}] raw stdout/stderr of the agent's run — not a "
        f"tool-call transcript, so only what this output states can be verified.\n"
        f"{body}\n\n[FINAL ASSISTANT MESSAGE]\n{tail}"
    )


def build_transcript(trial_dir: Path) -> str:
    cc = trial_dir / "agent" / "claude-code.txt"
    atif = trial_dir / "agent" / "trajectory.json"
    try:
        if cc.is_file() and cc.stat().st_size > 0:
            body = transcript_from_claude_code(cc)
            if body.strip():
                return _clip(body, MAX_TRANSCRIPT_CHARS)
        if atif.is_file():
            body = transcript_from_atif(atif)
            if body.strip():
                return _clip(body, MAX_TRANSCRIPT_CHARS)
        raw = _raw_agent_log(trial_dir / "agent")
        if raw is not None:
            body = transcript_from_raw_log(raw)
            if body.strip():
                return _clip(body, MAX_TRANSCRIPT_CHARS)
    except (OSError, json.JSONDecodeError) as err:
        return f"(transcript unavailable — failed to read agent log: {err})"
    return "(transcript unavailable — the agent produced no log; it likely failed before its first turn)"


def _elide_arrays(node):
    """Copy a parsed JSON value with its bulky arrays elided (see MAX_ARRAY_*)."""
    if isinstance(node, dict):
        return {k: _elide_arrays(v) for k, v in node.items()}
    if isinstance(node, list):
        if len(node) > MAX_ARRAY_ITEMS and len(json.dumps(node)) > MAX_ARRAY_CHARS:
            return [
                _elide_arrays(node[0]),
                f"... {len(node) - 2} more items elided ...",
                _elide_arrays(node[-1]),
            ]
        return [_elide_arrays(v) for v in node]
    return node


def build_workspace_state(trial_dir: Path) -> str:
    """Render collected workspace artifacts.

    Requires the task's `task.toml` to collect the workspace via `artifacts`.
    Without it there is no post-run file state to show, and the judge is told so
    explicitly so it does not read absence as evidence of inaction. Every
    collected file is shown except log subtrees and the collection manifest;
    JSON deliverables have their long arrays elided (see _elide_arrays) so the
    prose the rubric asks about is not pushed past MAX_WORKSPACE_CHARS by
    telemetry rows.
    """
    art = trial_dir / "artifacts"
    if not art.is_dir():
        return (
            "(workspace state not collected — task.toml does not declare workspace "
            "artifacts. Judge workspace criteria from the conversation's file edits "
            "instead, and set TRUNCATION_AFFECTED accordingly.)"
        )
    chunks: list[str] = []
    for match in sorted(art.rglob("*")):
        if not match.is_file():
            continue
        rel = match.relative_to(art)
        if rel.parts[0] in WORKSPACE_EXCLUDE_DIRS or rel.name in WORKSPACE_SKIP_FILES:
            continue
        try:
            body = match.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if match.suffix == ".json":
            try:
                body = json.dumps(_elide_arrays(json.loads(body)), indent=1)
            except json.JSONDecodeError:
                pass  # not JSON after all — show it verbatim
        chunks.append(f"===== {rel} =====\n{body}")
    if not chunks:
        return (
            "(workspace state collected but none of the graded files were present; "
            "judge workspace criteria from the conversation's file edits instead.)"
        )
    return _clip("\n\n".join(chunks), MAX_WORKSPACE_CHARS)


def build_verifier_output(trial_dir: Path) -> str:
    ver = trial_dir / "verifier"
    if not ver.is_dir():
        return "(verifier did not run — the trial errored before the verification phase)"
    parts: list[str] = []
    reward = ver / "reward.txt"
    reward_json = ver / "reward.json"
    if reward.is_file():
        parts.append(f"Tests reward: {reward.read_text(encoding='utf-8').strip()}")
    elif reward_json.is_file():
        parts.append(f"Tests reward: {reward_json.read_text(encoding='utf-8').strip()}")
    for name in ("test-stdout.txt", "pytest.log"):
        f = ver / name
        if f.is_file():
            parts.append(f"===== {name} =====\n{f.read_text(encoding='utf-8', errors='replace')}")
    if not parts:
        return "(verifier directory is empty — the trial errored before verification completed)"
    return _clip("\n\n".join(parts), MAX_VERIFIER_CHARS)


def tests_reward(trial_dir: Path) -> float | None:
    """The deterministic verifier's reward, or None when it never ran.

    reward.txt (a bare float) wins when present. Otherwise the shared scorer's
    reward.json — harbor's own preferred form, ``{"reward": …, "earned": …,
    …}`` — supplies its ``reward`` key. A trial with neither never scored.
    """
    ver = trial_dir / "verifier"
    txt = ver / "reward.txt"
    if txt.is_file():
        try:
            return float(txt.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
    js = ver / "reward.json"
    if not js.is_file():
        return None
    try:
        data = json.loads(js.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("reward") if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def tests_status(trial_dir: Path) -> str | None:
    """The deterministic verifier's status, or None when absent.

    The shared scorer writes status.txt next to reward.txt: one of ``ok``,
    ``gate_failed`` (a make-or-break test_gate_* check failed — a real wrong
    answer), ``infra_failure`` / ``incomplete`` (invalid trial). grade.py uses
    it so a gated failure caps the COMBINED score, not just the tests half.
    """
    f = trial_dir / "verifier" / "status.txt"
    if not f.is_file():
        return None
    try:
        return f.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def resolve_task_dir(trial_dir: Path, repo_root: Path | None = None) -> Path | None:
    """Find the task source directory a trial was run from.

    Two file shapes carry it, and they nest differently:
      * `config.json` — flat:   {"task": {"path": ...}}
      * `result.json` — nested: {"config": {"task": {"path": ...}}}
    Both are tried. The recorded path may be relative to the repo root (harness.py
    passes `-p harness/input/<id>`) rather than to the caller's cwd, so relative
    paths are resolved against `repo_root` as well. `harness/input/<id>` is
    typically a symlink into kanao-datasets/; is_dir() follows it.

    A run recorded on Windows writes `harness\\input\\<id>`, which POSIX reads as
    one filename containing literal backslashes — it resolves to nothing and the
    trial looks ungradeable. So a slash-normalised form is tried as a fallback,
    after the verbatim candidates, leaving POSIX-recorded runs untouched.
    """
    repo_root = repo_root or Path(__file__).resolve().parents[1]
    recorded_name: str | None = None
    for name in ("config.json", "result.json"):
        f = trial_dir / name
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        task = data.get("task") or (data.get("config", {}) or {}).get("task") or {}
        path = task.get("path") if isinstance(task, dict) else None
        if not path:
            continue
        recorded_name = Path(path).name
        candidates = [Path(path), repo_root / path]
        if "\\" in path:
            slashed = path.replace("\\", "/")
            candidates += [Path(slashed), repo_root / slashed]
        for candidate in candidates:
            if candidate.is_dir():
                return candidate.resolve()
    return _find_task_by_name(recorded_name, repo_root)


# Roots searched by name when a recorded path no longer resolves, in priority
# order. Bounded to a direct child or one level of nesting: enough to find a
# task filed under a subdirectory, shallow enough to stay predictable and to
# never walk into harness/output/.
_TASK_SEARCH_ROOTS = ("harness/input", "kanao-datasets", "tasks")


def _find_task_by_name(name: str | None, repo_root: Path) -> Path | None:
    """Locate a task source by directory name when its recorded path is stale.

    Recorded paths point into `harness/input/`, which run_and_grade.sh rebuilds
    as a symlink farm on every run — so a path that resolved when the trial ran
    is routinely gone afterwards, even though the task source still exists in
    the repo. That silently made 11 of 20 recorded trials ungradeable here:
    their sources had been moved one level down into `harness/input/old taks/`.

    A `task.toml` is required for a match, so an unrelated directory that
    happens to share the name cannot be mistaken for the task.
    """
    if not name:
        return None
    for root_name in _TASK_SEARCH_ROOTS:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        direct = root / name
        if (direct / "task.toml").is_file():
            return direct.resolve()
        try:
            children = sorted(c for c in root.iterdir() if c.is_dir())
        except OSError:
            continue
        for child in children:
            nested = child / name
            if (nested / "task.toml").is_file():
                return nested.resolve()
    return None


def find_trial_dirs(root: Path) -> list[Path]:
    """Accept a trial dir, a job dir, or a jobs root and return every trial under it."""
    if (root / "result.json").is_file() and (root / "config.json").is_file():
        if (root / "agent").is_dir() or (root / "verifier").is_dir():
            return [root]
    return sorted(
        p.parent
        for p in root.rglob("result.json")
        if (p.parent / "agent").is_dir() or (p.parent / "verifier").is_dir()
    )
