"""Judge council — rubric grading for DevOps-Gym tasks.

The aggregation rule is
carried over verbatim in behaviour:

  * **Unanimous** — every member voted AND all agree on SATISFIED → that verdict
    stands. ``resolved_by="unanimous"``.
  * Otherwise, if the **primary** member emitted a verdict for this criterion →
    the primary's verdict governs. This covers both a genuine Yes/No split and
    partial coverage where a smaller-context member truncated before reaching
    this index. ``resolved_by="primary"``.
  * Otherwise — no unanimity AND the primary itself cast no verdict → **Human
    Evaluation**: the index is added to ``abstention_flags``, counted in
    ``criteria_abstained``, and contributes 0 to the numerator.
    ``resolved_by="human_eval"``.

Scoring, also carried over: a satisfied positive criterion contributes its
weight to the numerator; a satisfied *negative* criterion (the forbidden
behaviour occurred) subtracts its weight. The denominator is the sum of
**positive** weights only — using the sum of all weights overshoots 1.0 whenever
penalties exist. ``overall_score`` is then clamped to [0.0, 1.0] so a rubric
score is always a fraction on the same scale as the tests' reward. The raw
quotient is kept beside it as ``overall_score_raw``, so a penalty heavy enough
to drive the score below zero stays visible instead of being erased.

Every criterion also carries ``justification``: the governing judge's own words
behind the resolved verdict, lifted out of ``rationales_by_judge`` so one answer
to "why this verdict" travels with the score. It is free of grader identity by
design — who governed is provenance, carried separately in ``resolved_by``.

Transport is stdlib urllib — no third-party dependency — and is routed per
seat by the model id's provider prefix:

  * bare id (``claude-opus-5``) or ``anthropic/…`` → Anthropic Messages API.
    Both credential styles this repo has in play are supported:
    ``ANTHROPIC_API_KEY`` (``x-api-key``) and ``CLAUDE_CODE_OAUTH_TOKEN``
    (``Authorization: Bearer`` plus the OAuth beta header).
  * ``openai/…`` (e.g. ``openai/gpt-5.6``) → OpenAI Chat Completions,
    authenticated with ``OPENAI_API_KEY``.

Each provider's base URL can be redirected on its own —
``JUDGE_OPENAI_BASE_URL`` / ``OPENAI_BASE_URL`` and the Anthropic pair — which
is how an ``openai/`` seat is pointed at the operator's local codex bridge
(``codex_bridge.py``) or a client's OpenAI-compatible gateway.

A mixed roster is the point of the split: three Claude seats grading a Claude
agent share their training and therefore their blind spots, so a cross-provider
seat is what turns an agreed verdict into evidence rather than an echo. The
dispatch mirrors WildClawBench's ``src/utils/grading.py:_call_one_judge``,
which routes ``bedrock/`` vs OpenAI the same way.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from judge.evidence import TRUNCATION_MARKER

logger = logging.getLogger("judge.council")

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
OAUTH_BETA = "oauth-2025-04-20"

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# --------------------------------------------------------------------------- #
# Transport routing — provider base-URL overrides
# --------------------------------------------------------------------------- #
# Provider base-URL overrides. Deliberately NOT the SDK-standard names
# (OPENAI_BASE_URL / ANTHROPIC_BASE_URL): a shell that exports those for some
# other tool — this machine's own Claude Code proxy, say — would silently
# redirect every judge call, and harbor's codex *agent* reads OPENAI_BASE_URL
# too, so one variable for both would route the agent under test through the
# judge's transport. JUDGE_OPENAI_BASE_URL is how an openai/ seat is pointed at
# the operator's codex bridge (codex_bridge.py); inside the verifier container
# scripts/rubric_judge_cli.py maps the task's declared OPENAI_BASE_URL onto it,
# where no stray shell variable can reach.
OPENAI_BASE_URL_ENVS = ("JUDGE_OPENAI_BASE_URL",)
ANTHROPIC_BASE_URL_ENVS = ("JUDGE_ANTHROPIC_BASE_URL",)
# Same split for the credential: the bridge accepts any bearer, so the judge
# can carry a placeholder here while OPENAI_API_KEY stays the agent's real key.
OPENAI_API_KEY_ENVS = ("JUDGE_OPENAI_API_KEY", "OPENAI_API_KEY", "KENSEI_OPENAI_API_KEY")


def provider_base_url(envs: tuple[str, ...]) -> str:
    """First non-empty base URL among `envs`, or "" for the provider default.

    The OpenAI SDK convention includes ``/v1`` in the base (``…/v1``); the
    Anthropic one and most gateways do not. Both are accepted: a trailing
    ``/v1`` is dropped and the endpoint path is appended once by the caller.
    """
    for name in envs:
        raw = os.environ.get(name, "").strip().rstrip("/")
        if raw:
            return raw[:-3] if raw.endswith("/v1") else raw
    return ""


def anthropic_messages_url() -> str:
    base = provider_base_url(ANTHROPIC_BASE_URL_ENVS)
    return f"{base}/v1/messages" if base else API_URL


def openai_chat_url() -> str:
    base = provider_base_url(OPENAI_BASE_URL_ENVS)
    return f"{base}/v1/chat/completions" if base else OPENAI_API_URL


def transport_summary() -> str:
    """One phrase for banners: where the HTTP seats send their calls."""
    overrides = [
        f"{label} via {url}"
        for label, url in (
            ("openai", provider_base_url(OPENAI_BASE_URL_ENVS)),
            ("anthropic", provider_base_url(ANTHROPIC_BASE_URL_ENVS)),
        )
        if url
    ]
    return ", ".join(overrides) if overrides else "direct to providers"

# Providers that may appear as an explicit `provider/` prefix on a judge model
# id. Anything else — including a bare id — is Anthropic, because Anthropic ids
# are the bulk of every roster and writing them unprefixed keeps the common case
# quiet. (WildClawBench defaults the other way, to OpenAI, since that is its
# common case; the convention is the same, only the default differs.)
_KNOWN_PROVIDERS = frozenset({"anthropic", "openai", "codex"})

# `codex/<model>` runs the locally-installed Codex CLI as a subprocess instead
# of calling an API. It spends the operator's ChatGPT subscription quota rather
# than metered API credit, which is the whole reason it exists: it needs no
# OPENAI_API_KEY, only a `codex login` this machine already holds.
CODEX_EXEC_ARGS = ("exec", "--json", "--sandbox", "read-only", "--skip-git-repo-check")

# Measured 2026-09-01 on codex-cli 0.146.0: a trivial one-line prompt still
# reported input_tokens=14564 (cached_input_tokens=11008). That is Codex's own
# agent scaffold, carried on EVERY call on top of whatever evidence we send.
# It is quota, not dollars, but it makes this seat's token counts
# non-comparable with an `openai/` seat's for the same rubric.
CODEX_SCAFFOLD_TOKENS_OBSERVED = 14_564

# Subscription OAuth tokens are gated to Claude Code traffic: requests whose
# system prompt does not open with this exact identity block are rejected by
# the larger models with an opaque 429 "rate_limit_error" (haiku is exempt,
# which is why a council on OAuth credentials degraded to a lone haiku seat).
# The auth bridge injects the same preamble for agent traffic.
CC_PREAMBLE = "You are Claude Code, Anthropic's official CLI for Claude."

PROMPT_DIR = Path(__file__).resolve().parent / "system_prompts"

# Canonical council order. The FIRST entry is the primary — the tiebreaker and
# source of truth on any non-unanimous criterion. Override the whole roster with
# JUDGE_COUNCIL_MEMBERS="primary=<model>,second=<model>,third=<model>".
#
# Default is a single GPT-5.6 seat driven through the locally-installed Codex
# CLI (`codex/…`), so grading spends the operator's ChatGPT subscription quota
# (a `codex login` this machine already holds) rather than metered API credit
# and needs no OPENAI_API_KEY. A single seat means GPT-5.6 alone decides every
# criterion — set JUDGE_COUNCIL_MEMBERS to restore a multi-seat Claude council.
#
# The id is `gpt-5.6-sol`: codex-cli on a ChatGPT account rejects the bare
# `gpt-5.6` alias ("model is not supported when using Codex with a ChatGPT
# account") and only accepts `gpt-5.6-sol`. pricing.py keys both, so finance
# reporting is unaffected by the exact spelling.
MODEL_PIN_ENV = "JUDGE_MODEL_PIN"
DEFAULT_MEMBERS: list[tuple[str, str]] = [
    ("primary", "codex/gpt-5.6-sol"),
]

MAX_OUTPUT_TOKENS = 16000

# --------------------------------------------------------------------------- #
# Evidence headroom
#
# Every judge gets the same evidence, but they don't have the same context
# window: sending one payload sized to nobody in particular either wastes the
# big models' capacity or overflows the small one. Each member instead gets a
# payload sized to its own ceiling:
#
#   budget_chars = (context_window − MAX_OUTPUT_TOKENS − scaffold) × chars/token
#
# chars/token is measured on this repo's own evidence (transcripts of tool calls
# and Terraform/Bicep diffs came out at ~3.24), rounded down to 3.0 so tokenizer
# drift eats margin rather than context. Scaffold is the system prompt, the
# rubric block and the prompt template.
#
# Export JUDGE_MAX_EVIDENCE=<chars> to cap every member (the operator stays in
# control of the maximum any single judge call can cost); 0 restores unbounded.
# --------------------------------------------------------------------------- #

_CHARS_PER_TOKEN = 3.0
_SCAFFOLD_TOKENS = 6_000

# Context windows in tokens. Matched as substrings against the model id as
# passed, so a JUDGE_COUNCIL_MEMBERS override still maps to the right budget.
_MODEL_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("claude-haiku-4-5", 200_000),
    ("claude-opus-4-8", 1_000_000),
    ("claude-opus-5", 1_000_000),
    ("claude-sonnet-5", 1_000_000),
    ("claude-sonnet-4-6", 1_000_000),
    # GPT-5.6 Sol. Substring-matched, so this one entry covers both the
    # `gpt-5.6-sol` id and its `gpt-5.6` alias.
    #
    # 944_000, not the headline 1,050,000 context window: Sol splits that into
    # 922K max INPUT and 128K max output, and input is the binding constraint
    # here. Feeding _budget_from_context the full window would authorise
    # ~1,028,000 tokens of evidence and 400 on anything that actually used it;
    # 944_000 (922K + MAX_OUTPUT_TOKENS + _SCAFFOLD_TOKENS) makes the formula
    # land exactly on the input cap instead.
    ("gpt-5.6", 944_000),
)

# Fallback for an unrecognised model: the smallest window in the table above, so
# an unknown judge is sized conservatively rather than handed a payload it will
# 400 on.
_DEFAULT_CONTEXT_WINDOW = 200_000


def _budget_from_context(window_tokens: int) -> int:
    return int((window_tokens - MAX_OUTPUT_TOKENS - _SCAFFOLD_TOKENS) * _CHARS_PER_TOKEN)


def _env_max_evidence() -> int | None:
    """JUDGE_MAX_EVIDENCE as an int, or None when unset/blank/unparseable.

    A value <= 0 means "unbounded" and is returned as 0 so callers can tell it
    apart from "not set".
    """
    raw = os.environ.get("JUDGE_MAX_EVIDENCE", "").strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("ignoring unparseable JUDGE_MAX_EVIDENCE=%r", raw)
        return None


def member_evidence_budget(model: str) -> int | None:
    """Evidence budget in characters for one member. None means unbounded."""
    override = _env_max_evidence()
    if override is not None:
        return override or None
    for needle, window in _MODEL_CONTEXT_WINDOWS:
        if needle in model:
            return _budget_from_context(window)
    return _budget_from_context(_DEFAULT_CONTEXT_WINDOW)


def fit_evidence(
    transcript: str, workspace: str, verifier: str, budget: int | None
) -> tuple[str, str, str]:
    """Trim the evidence blocks to `budget` total characters.

    Sheds from the transcript first and the verifier output last: the transcript
    is the most redundant (repeated tool output), while the verifier's pass/fail
    detail is what most criteria actually turn on. Truncation is marked so the
    judge can set TRUNCATION_AFFECTED honestly.
    """
    blocks = [transcript, workspace, verifier]
    if budget is None:
        return transcript, workspace, verifier
    over = sum(len(b) for b in blocks) - budget
    if over <= 0:
        return transcript, workspace, verifier
    for i, block in enumerate(blocks):
        if over <= 0:
            break
        # The marker is appended after cutting, so it costs characters too — cut
        # that much extra, and never shrink a block below the marker itself
        # (which would make a short block grow instead of shrink).
        removable = max(0, len(block) - len(TRUNCATION_MARKER))
        reduce_by = min(over, removable)
        if reduce_by:
            keep = len(block) - reduce_by - len(TRUNCATION_MARKER)
            blocks[i] = block[:keep] + TRUNCATION_MARKER
            over -= reduce_by
    return blocks[0], blocks[1], blocks[2]


@dataclass(frozen=True)
class CouncilMember:
    """A council judge: stable `family` label + the model id it currently maps to.

    The family is what downstream keys are built from, so swapping which model
    backs a seat does not change the shape of score.json.
    """

    family: str
    model: str


@dataclass
class JudgeResult:
    family: str
    model: str
    ok: bool
    verdicts: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    error: str = ""
    raw_response: str = ""


# --------------------------------------------------------------------------- #
# Roster
# --------------------------------------------------------------------------- #


def council_members() -> list[CouncilMember]:
    """Resolve the roster, reading env live.

    ``JUDGE_COUNCIL_MEMBERS`` ("family=model" CSV) overrides the defaults. The
    first member in the resulting list is the primary/tiebreaker.
    """
    raw = os.environ.get("JUDGE_COUNCIL_MEMBERS", "").strip()
    if not raw:
        pin = os.environ.get(MODEL_PIN_ENV, "").strip()
        return [CouncilMember(f, pin or m) for f, m in DEFAULT_MEMBERS]
    members: list[CouncilMember] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ValueError(
                f"JUDGE_COUNCIL_MEMBERS entry {entry!r} must use 'family=model' syntax"
            )
        fam, _, model = entry.partition("=")
        fam, model = fam.strip(), model.strip()
        if not fam or not model:
            raise ValueError(f"JUDGE_COUNCIL_MEMBERS entry {entry!r} is incomplete")
        members.append(CouncilMember(fam, model))
    if not members:
        raise ValueError("JUDGE_COUNCIL_MEMBERS is set but resolved to an empty roster")
    return members


_PIN_SUFFIX = re.compile(r"-(?:\d{4}-\d{2}-\d{2}|\d{6,})$")


def judge_pin_status() -> dict:
    """Report whether every judge seat runs a pinned model version.

    Returns {"pinned": bool, "seats": [{"family","model","pinned"}...]}.
    """
    seats = []
    for m in council_members():
        model = m.model.split("/")[-1]  # strip provider prefix (codex/, anthropic/)
        seats.append({"family": m.family, "model": m.model,
                      "pinned": bool(_PIN_SUFFIX.search(model))})
    return {"pinned": all(s["pinned"] for s in seats), "seats": seats}


@dataclass(frozen=True)
class MemberHealth:
    """One council seat's preflight result."""

    family: str
    model: str
    ok: bool
    detail: str


_HEALTH_SYSTEM = "You are a preflight probe. Answer with a single word."
_HEALTH_USER = "Reply with exactly: OK"


def council_health(timeout: int = 60, max_attempts: int = 1) -> list[MemberHealth]:
    """Probe every council seat with one minimal call. Never raises.

    An unreachable council does not fail loudly — it fails *quietly and
    expensively*. A member that cannot be reached simply casts no vote; with no
    votes, every criterion falls through to `human_eval` and the trial lands at
    rubric 0.0 with `criteria_abstained == criteria_total`. On paper that is
    indistinguishable from a genuinely terrible run, so the breakage is only
    discovered by opening score.json — after a full grading pass has been paid
    for. One cheap call per seat up front tells the two apart.

    Subscription OAuth is the usual culprit and the reason this cannot be a
    single probe: the large models reject non-Claude-Code traffic with an opaque
    429 while haiku is exempt (see CC_PREAMBLE above), so a broken roster still
    answers on one seat and looks half-alive. Every seat is therefore probed.

    Retries default off: preflight should report a rate limit in seconds rather
    than absorb it over four backoffs, and a 429 here is a finding, not noise.

    On a mixed-provider roster this is also the check that tells the operator
    *which* credential is missing: each seat resolves its own before dialling.
    """
    try:
        members = council_members()
    except ValueError as err:
        return [MemberHealth("roster", "", False, str(err))]
    def probe(member: CouncilMember) -> MemberHealth:
        # Credential first, and per seat: a mixed roster has two independent
        # credentials, so a missing OPENAI_API_KEY must fail the GPT seat only
        # and leave the Claude seats reporting the truth about themselves.
        # Checked before the call so "no key" reads as "no key" rather than as
        # whatever HTTP status the provider returns for an empty Authorization.
        cred_err = credential_error(member.model)
        if cred_err:
            return MemberHealth(member.family, member.model, False, cred_err)
        started = time.monotonic()
        try:
            text, usage = call_one_judge(
                member.model, _HEALTH_SYSTEM, _HEALTH_USER,
                timeout=timeout, max_attempts=max_attempts,
            )
        except Exception as err:  # noqa: BLE001 — any failure means an unusable seat
            return MemberHealth(member.family, member.model, False, str(err)[:200])
        if not text.strip():
            return MemberHealth(member.family, member.model, False, "empty response")
        return MemberHealth(
            member.family, member.model, True,
            f"{text.strip()[:16]!r} · {usage['output_tokens']} out tok · "
            f"{time.monotonic() - started:.1f}s",
        )

    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        return list(pool.map(probe, members))


def council_health_summary(health: list[MemberHealth]) -> tuple[bool, str]:
    """Collapse a probe to (usable, one-line reason).

    "Usable" is not "all seats up". The aggregation rule means a live primary
    can carry the council alone — it governs every non-unanimous criterion — so
    a downed secondary costs unanimity, not correctness. A downed *primary* is
    fatal: without it, any split criterion abstains. Blocking on a degraded but
    correct council would be over-strict; passing a headless one silently is the
    bug this whole check exists to prevent.
    """
    if not health:
        return False, "no council members resolved"
    down = [h for h in health if not h.ok]
    if not down:
        return True, f"{len(health)}/{len(health)} seats reachable"
    primary_down = any(not h.ok for h in health[:1])
    names = ", ".join(f"{h.family}({h.model})" for h in down)
    if primary_down or len(down) == len(health):
        return False, f"{len(down)}/{len(health)} seats unreachable: {names}"
    return True, f"degraded — {len(down)}/{len(health)} unreachable: {names}; primary still votes"


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #


def _judge_system_prompt() -> str:
    return (PROMPT_DIR / "judge_system.md").read_text(encoding="utf-8")


def prompt_versions() -> dict[str, str]:
    """SHA-256 (first 12 hex chars) of each prompt file the judge uses.

    Recorded into every score so a rubric result is self-describing: anyone
    reading score.json can tell exactly which prompt bytes produced it, and a
    later regrade can confirm it ran against the same prompts. A missing file
    is reported as "absent" rather than raising, so scoring is never blocked by
    version bookkeeping.
    """
    versions: dict[str, str] = {}
    for name in ("judge_system.md", "judge_user.md"):
        path = PROMPT_DIR / name
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        except OSError:
            digest = "absent"
        versions[name] = digest
    return versions


def _rubrics_block(rubrics: list[dict]) -> str:
    lines = []
    for i, r in enumerate(rubrics, start=1):
        text = r.get("criterion", "") if isinstance(r, dict) else str(r)
        lines.append(f"{i}. {text}")
    return "\n".join(lines)


def _judge_user_prompt(
    task_description: str,
    rubrics: list[dict],
    transcript: str,
    workspace_state: str,
    verifier_output: str,
) -> str:
    template = (PROMPT_DIR / "judge_user.md").read_text(encoding="utf-8")
    return (
        template.replace("{task_description}", task_description)
        .replace("{transcript}", transcript)
        .replace("{workspace_state}", workspace_state)
        .replace("{verifier_output}", verifier_output)
        .replace("{rubrics_block}", _rubrics_block(rubrics))
        .replace("{n_criteria}", str(len(rubrics)))
    )


# --------------------------------------------------------------------------- #
# Weights and polarity
# --------------------------------------------------------------------------- #


def _extract_weight(r: dict) -> float:
    """Weight from `weight`, falling back to `score`, defaulting to 1.0."""
    w = r.get("weight")
    if w is None:
        w = r.get("score")
    if w is None:
        return 1.0
    try:
        return float(w)
    except (TypeError, ValueError):
        return 1.0


def _criterion_pass_from_satisfied(satisfied: bool, weight: float) -> bool:
    """SATISFIED reflects the literal criterion text, NOT the point sign.

    positive weight + satisfied=True  → passed (desired thing done)
    positive weight + satisfied=False → failed
    negative weight + satisfied=False → passed (guardrail held)
    negative weight + satisfied=True  → failed (forbidden behaviour occurred)
    """
    return (not satisfied) if weight < 0 else satisfied


HUMAN_EVAL_JUSTIFICATION = (
    "No verdict was reached: the council did not agree and the primary judge "
    "cast no vote, so this criterion is deferred to human evaluation."
)


def _governing_rationale(
    resolved_by: str,
    primary_idx: int | None,
    per_rationale: list[str],
    per_voted: list[bool],
) -> str:
    """The rationale behind the resolved verdict — the score's justification.

    ``unanimous`` and ``primary`` both resolve to the primary's own rationale:
    unanimity requires full coverage, so the primary has necessarily voted, and
    on a split the primary's vote is what governs. The scan for another voter is
    reachable only with a roster that seats no primary, which cannot happen in
    practice but must not raise here.

    Grader identity is deliberately absent from the returned text. Callers that
    want "who decided this" read ``resolved_by`` and ``judges``; callers that
    ship outcomes without provenance can carry this string on its own.
    """
    if resolved_by == "human_eval":
        return HUMAN_EVAL_JUSTIFICATION
    if primary_idx is not None and primary_idx < len(per_voted) and per_voted[primary_idx]:
        return per_rationale[primary_idx]
    for rationale, voted in zip(per_rationale, per_voted):
        if voted:
            return rationale
    return ""


# --------------------------------------------------------------------------- #
# Verdict parsing
# --------------------------------------------------------------------------- #

# TRUNCATION_AFFECTED is OPTIONAL so a judge that omits it does not collapse the
# whole verdict count and trigger a false abstention. Defaults to No when absent.
# This regex is coupled to the verdict template in judge_system.md — changing one
# without the other silently zeros every vote.
_VERDICT_RE = re.compile(
    r"\d+\.\s.*?"
    r"\[\[\s*RATIONALE:\s*(.*?)\s*\]\]\s*"
    r"\[\[\s*SATISFIED:\s*(Yes|No)\s*\]\]"
    r"(?:\s*\[\[\s*TRUNCATION_AFFECTED:\s*(Yes|No)\s*\]\])?",
    re.DOTALL | re.IGNORECASE,
)


def parse_verdict_text(response: str, n_criteria: int) -> list[dict]:
    """Parse a judge response into verdict dicts.

    A short list is returned as-is rather than raising: a smaller-context member
    may truncate before covering every criterion, and the aggregator votes
    per-criterion using only the members that actually reached that index.
    Abstentions must never be invented as No-votes the model did not cast.
    """
    if not response:
        raise ValueError(f"empty judge response (expected up to {n_criteria} verdicts)")
    matches = _VERDICT_RE.findall(response)
    if not matches:
        raise ValueError(
            f"no verdicts parsed (expected up to {n_criteria}); "
            "response shape does not match the verdict template"
        )
    out: list[dict] = []
    for rationale, satisfied, truncation in matches[:n_criteria]:
        out.append(
            {
                "rationale": (rationale or "").strip(),
                "satisfied": (satisfied or "No").strip().lower() == "yes",
                "truncation_affected": (truncation or "No").strip().lower() == "yes",
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #


def _keychain_oauth_token() -> str:
    """Borrow harness.py's credential cascade (Keychain → ~/.claude → OpenCode).

    Without this the judge would demand a separate credential even though the
    machine is already logged in — the same gap that made harness.py's bridge
    useless before it learned to read the Keychain. Imported lazily so `judge`
    stays usable if harness.py is absent or fails to import.
    """
    try:
        import sys as _sys
        from pathlib import Path as _Path

        # harness.py re-execs into the venv at import time; without this guard it
        # would replace THIS process, re-running harness.py with our argv.
        os.environ.setdefault("DK_HARNESS_REEXEC", "1")
        _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
        import harness  # noqa: PLC0415

        return harness.TokenManager().access_token()
    except Exception:  # noqa: BLE001 — any failure just means "no credential here"
        return ""


def _auth_headers() -> dict[str, str]:
    """Build auth headers from whichever credential is present.

    Precedence: ANTHROPIC_API_KEY → CLAUDE_CODE_OAUTH_TOKEN → the logged-in
    credential harness.py resolves. An API key wins when several are available,
    matching harbor's own precedence.
    """
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if api_key:
        return {"x-api-key": api_key}
    oauth = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip() or _keychain_oauth_token()
    if oauth:
        return {"Authorization": f"Bearer {oauth}", "anthropic-beta": OAUTH_BETA}
    raise RuntimeError(
        "No judge credential found. Log in with `claude`, or set "
        "ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN before grading."
    )


def _openai_auth_headers() -> dict[str, str]:
    """Auth header for an OpenAI seat.

    ``JUDGE_OPENAI_API_KEY`` first (the judge-scoped name, see
    OPENAI_API_KEY_ENVS), then ``OPENAI_API_KEY``, then ``KENSEI_OPENAI_API_KEY``
    — the last is what WildClawBench reads, accepted here so an operator moving
    between the two repos does not have to re-export a key under a second name.

    There is deliberately no Keychain fallback as there is for Anthropic: no
    OpenAI credential is resolvable from a `claude` login, so a missing key is a
    hard, immediately-legible failure rather than a seat that quietly abstains.
    """
    key = next(
        (v for v in (os.environ.get(name, "").strip() for name in OPENAI_API_KEY_ENVS) if v),
        "",
    )
    if not key:
        raise RuntimeError(
            "No OpenAI credential found for this judge seat. Set OPENAI_API_KEY "
            "(or JUDGE_OPENAI_API_KEY) in .env or the environment before grading "
            "with an `openai/` member."
        )
    return {"Authorization": f"Bearer {key}"}


def _codex_cli() -> str:
    """Path to the Codex CLI, or "" when it is not installed."""
    return shutil.which("codex") or ""


def _codex_credential_error() -> str:
    """Why the Codex CLI is unusable as a judge seat, or "" if it is ready.

    Asks the CLI itself (`codex login status`) rather than reading the
    credential store: the answer is what actually matters, and no secret has to
    be touched to get it.
    """
    cli = _codex_cli()
    if not cli:
        return "codex CLI not found on PATH; install it or drop the `codex/` seat"
    try:
        proc = subprocess.run(
            [cli, "login", "status"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as err:
        return f"could not run `codex login status`: {err}"
    out = f"{proc.stdout} {proc.stderr}".strip()
    if proc.returncode != 0 or "logged in" not in out.lower():
        return f"codex CLI is not logged in (`codex login`): {out[:120]}"
    return ""


def _split_provider(model: str) -> tuple[str, str]:
    """Split a judge model id into (provider, bare model id).

    An unprefixed id is Anthropic — see _KNOWN_PROVIDERS. A prefix we do not
    recognise is left alone rather than stripped, so a typo surfaces as an
    Anthropic 404 naming the bad model instead of being silently routed.
    """
    m = (model or "").strip()
    provider, sep, rest = m.partition("/")
    if sep and provider in _KNOWN_PROVIDERS and rest:
        return provider, rest
    return "anthropic", m


def _display_model(model: str) -> str:
    """The model id as recorded in outputs — provider transport prefix stripped.

    `codex/` and `openai/` are routing hints for THIS process (which endpoint or
    CLI a seat dials), not part of the model's identity. Recording
    `codex/gpt-5.6-sol` would leak the transport into score.json/usage.json; the
    graded model is `gpt-5.6-sol`. Bare/Anthropic ids are unchanged.
    """
    _, bare = _split_provider(model)
    return bare


def credential_error(model: str) -> str:
    """Why this seat's credential is unusable, or "" if it resolves.

    Checked without a network call so `council_health` can tell a missing key
    apart from an unreachable endpoint, and — since it is per seat — so a
    missing OpenAI key cannot condemn the Claude seats or vice versa.
    """
    provider, _ = _split_provider(model)
    if provider == "codex":
        return _codex_credential_error()
    try:
        if provider == "openai":
            _openai_auth_headers()
        else:
            _auth_headers()
    except RuntimeError as err:
        return str(err)
    return ""


# Transient statuses worth retrying. 429 is the big one: the judges share the
# operator's subscription with the agent run that just finished, so the first
# council volley routinely lands inside a hot rate-limit window. Without
# retries a single 429 silently reduces the council to abstention.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 529})
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SEC = 15.0
_BACKOFF_CAP_SEC = 120.0


def _with_retries(model: str, timeout: int, max_attempts: int, send):
    """Call `send(timeout)` with backoff on transient HTTP/connection failures.

    One retry policy for every provider. 429 is the big one: the judges share
    the operator's subscription with the agent run that just finished, so the
    first council volley routinely lands inside a hot rate-limit window.
    Without retries a single 429 silently reduces the council to abstention.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return send(timeout)
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")[:400]
            if err.code in _RETRYABLE_STATUSES and attempt < max_attempts:
                retry_after = (err.headers.get("retry-after") or "").strip()
                delay = (
                    float(retry_after)
                    if retry_after.replace(".", "", 1).isdigit()
                    else min(_BACKOFF_BASE_SEC * 2 ** (attempt - 1), _BACKOFF_CAP_SEC)
                )
                logger.warning(
                    "judge %s: HTTP %s (attempt %d/%d), retrying in %.0fs",
                    model, err.code, attempt, max_attempts, delay,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"HTTP {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            if attempt < max_attempts:
                delay = min(_BACKOFF_BASE_SEC * 2 ** (attempt - 1), _BACKOFF_CAP_SEC)
                logger.warning(
                    "judge %s: %s (attempt %d/%d), retrying in %.0fs",
                    model, err.reason, attempt, max_attempts, delay,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"connection error: {err.reason}") from err
    raise RuntimeError(f"judge {model}: exhausted {max_attempts} attempts")


def _call_judge_anthropic(
    model: str, system: str, user: str, timeout: int, max_attempts: int
) -> tuple[str, dict]:
    """One Anthropic Messages API call. Returns (text, usage)."""
    headers = {
        "content-type": "application/json",
        "anthropic-version": API_VERSION,
        **_auth_headers(),
    }
    # OAuth credentials require the Claude Code identity preamble as the first
    # system block; an API key (x-api-key) needs no such gate.
    system_payload: str | list = system
    if "Authorization" in headers:
        system_payload = [
            {"type": "text", "text": CC_PREAMBLE},
            {"type": "text", "text": system},
        ]
    body = json.dumps(
        {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": system_payload,
            "messages": [{"role": "user", "content": user}],
        }
    ).encode("utf-8")

    def send(t: int) -> dict:
        req = urllib.request.Request(
            anthropic_messages_url(), data=body, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=t) as resp:
            return json.loads(resp.read().decode("utf-8"))

    payload = _with_retries(model, timeout, max_attempts, send)

    text = "".join(
        b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
    )
    u = payload.get("usage", {}) or {}
    usage = {
        "input_tokens": int(u.get("input_tokens", 0) or 0),
        "output_tokens": int(u.get("output_tokens", 0) or 0),
        "cache_read_tokens": int(u.get("cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(u.get("cache_creation_input_tokens", 0) or 0),
        "request_count": 1,
    }
    if payload.get("stop_reason") == "refusal":
        raise RuntimeError("judge refused the request (stop_reason=refusal)")
    return text, usage


def _call_judge_openai(
    model: str, system: str, user: str, timeout: int, max_attempts: int
) -> tuple[str, dict]:
    """One OpenAI Chat Completions call, streamed. Returns (text, usage).

    Three details here are load-bearing, all of them established in
    WildClawBench's `src/utils/grading.py:_call_judge_openai` and reproduced
    rather than rediscovered:

      * ``max_completion_tokens``, NOT ``max_tokens`` — the gpt-5 reasoning
        models reject the latter outright.
      * NO ``response_format={"type": "json_object"}``. It wraps the reply in a
        JSON envelope, which `_VERDICT_RE` cannot match; every criterion then
        parses as a non-vote and the seat abstains across the whole rubric
        without erroring — the exact silent failure this council is built to
        avoid.
      * ``stream_options.include_usage``, because a streamed response otherwise
        carries no usage block at all and this seat would report zero tokens
        into the finance payload.

    `CC_PREAMBLE` is deliberately NOT applied. It is an Anthropic OAuth gate,
    and prepending "You are Claude Code" to a GPT judge's system prompt would
    change what the model believes it is while it grades.
    """
    headers = {
        "content-type": "application/json",
        "accept": "text/event-stream",
        **_openai_auth_headers(),
    }
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ).encode("utf-8")

    def send(t: int) -> tuple[str, dict]:
        req = urllib.request.Request(
            openai_chat_url(), data=body, headers=headers, method="POST"
        )
        parts: list[str] = []
        seen_usage: dict = {}
        with urllib.request.urlopen(req, timeout=t) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if isinstance(piece, str):
                        parts.append(piece)
                u = obj.get("usage")
                if isinstance(u, dict):
                    seen_usage = u
        return "".join(parts), seen_usage

    text, u = _with_retries(model, timeout, max_attempts, send)

    details = u.get("prompt_tokens_details") or {}
    prompt_tok = int(u.get("prompt_tokens", 0) or 0)
    cached_tok = int(details.get("cached_tokens", 0) or 0)
    usage = {
        # OpenAI's `prompt_tokens` INCLUDES the cached tokens; Anthropic's
        # `input_tokens` excludes them and reports cache reads separately.
        # judge/pricing.py prices the two fields as disjoint, so the cached
        # portion is subtracted here to normalise onto the Anthropic
        # convention — without it every OpenAI seat over-reports both its
        # input tokens and its cost.
        "input_tokens": max(0, prompt_tok - cached_tok),
        "output_tokens": int(u.get("completion_tokens", 0) or 0),
        "cache_read_tokens": cached_tok,
        # OpenAI charges nothing to populate its cache, so there is no write
        # side to report. The key stays for shape-compatibility with the
        # Anthropic path — score.json must not vary by provider.
        "cache_write_tokens": 0,
        "request_count": 1,
    }
    return text, usage


def _call_judge_codex(
    model: str, system: str, user: str, timeout: int, max_attempts: int
) -> tuple[str, dict]:
    """One judge verdict via the local Codex CLI. Returns (text, usage).

    Spends the operator's ChatGPT subscription quota, not API credit, so this
    seat needs no OPENAI_API_KEY — only a `codex login`. It drives the CLI the
    way it is meant to be driven (`codex exec`), rather than extracting the
    stored credential and replaying it at api.openai.com: that credential is
    scoped to Codex traffic, and impersonating the Codex client to get around
    that is neither reliable nor ours to do.

    Two consequences worth knowing at the call site:

      * Codex is an AGENT, not a completion endpoint. It is pinned to a
        read-only sandbox and run in a scratch cwd so a judge cannot mutate the
        trial it is grading, but it may still take a turn or two to answer, so
        this seat is slower than an HTTP one.
      * Every call carries Codex's own ~14.5K-token scaffold
        (CODEX_SCAFFOLD_TOKENS_OBSERVED), on top of the evidence.
    """
    cli = _codex_cli()
    if not cli:
        raise RuntimeError("codex CLI not found on PATH")
    # `codex exec` takes a single prompt with no system role, so the judge's
    # system prompt is prepended. The separator keeps the rubric instructions
    # visibly distinct from the evidence rather than running them together.
    prompt = f"{system}\n\n---\n\n{user}"

    def attempt() -> tuple[str, dict]:
        proc = subprocess.run(
            [cli, *CODEX_EXEC_ARGS, "-m", model],
            input=prompt, capture_output=True, text=True, timeout=timeout,
            # A scratch cwd, so a read-only agent cannot even read the trial
            # tree it is judging and mistake it for its own workspace.
            cwd=tempfile.gettempdir(),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"codex exec exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:300]}"
            )
        message, usage_obj = "", {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                # Keep the LAST message: an agent turn may narrate before it
                # delivers the verdict block.
                message = item["text"]
            if event.get("type") == "turn.completed":
                usage_obj = event.get("usage") or {}
        if not message.strip():
            raise RuntimeError("codex exec produced no agent message")
        return message, usage_obj

    last: Exception | None = None
    for n in range(1, max_attempts + 1):
        try:
            text, u = attempt()
            break
        except (RuntimeError, OSError, subprocess.SubprocessError) as err:
            last = err
            if n >= max_attempts:
                raise RuntimeError(f"codex judge failed: {err}") from err
            delay = min(_BACKOFF_BASE_SEC * 2 ** (n - 1), _BACKOFF_CAP_SEC)
            logger.warning(
                "judge codex/%s: %s (attempt %d/%d), retrying in %.0fs",
                model, str(err)[:120], n, max_attempts, delay,
            )
            time.sleep(delay)
    else:  # pragma: no cover - the loop either breaks or raises
        raise RuntimeError(f"codex judge failed: {last}")

    cached = int(u.get("cached_input_tokens", 0) or 0)
    total_in = int(u.get("input_tokens", 0) or 0)
    usage = {
        # Assumed inclusive of `cached_input_tokens`, matching OpenAI's API
        # convention and this payload's field naming. Two probes (a trivial
        # prompt and one ~5.7K tokens larger) moved input_tokens by exactly the
        # payload while cached held at 11008, which is consistent with either
        # reading — so this is a documented assumption, not a measurement. If
        # it is wrong the effect is a modest UNDER-count of this seat's input
        # tokens; grading itself is unaffected either way.
        "input_tokens": max(0, total_in - cached),
        "output_tokens": int(u.get("output_tokens", 0) or 0),
        "cache_read_tokens": cached,
        "cache_write_tokens": int(u.get("cache_write_input_tokens", 0) or 0),
        "request_count": 1,
    }
    return text, usage


def call_one_judge(
    model: str,
    system: str,
    user: str,
    timeout: int = 600,
    max_attempts: int = _MAX_ATTEMPTS,
) -> tuple[str, dict]:
    """One judge call, routed by provider. Returns (text, usage).

    Raises on transport failure. Retries transient failures (429/5xx/connection)
    with exponential backoff, honouring the server's retry-after header when
    present. `max_attempts=1` disables retries — preflight probes want the first
    failure reported in seconds rather than absorbed over four backoffs.

    This stays the single entry point for every provider: `council_health`,
    `_run_council` and the tests all go through it, so a new backend is added by
    extending the dispatch below and nothing else moves.
    """
    provider, bare = _split_provider(model)
    if provider == "openai":
        return _call_judge_openai(bare, system, user, timeout, max_attempts)
    if provider == "codex":
        return _call_judge_codex(bare, system, user, timeout, max_attempts)
    return _call_judge_anthropic(bare, system, user, timeout, max_attempts)


def _run_council(
    members: list[CouncilMember],
    system: str,
    build_user: Callable[[str], str],
    n_criteria: int,
) -> list[JudgeResult]:
    """Run every member in parallel. Never raises — a failed member is recorded
    as ``ok=False`` so the aggregator can treat it as an abstention.

    ``build_user`` is called per member with that member's model id, so each
    judge gets a payload sized to its own context window (see member_evidence_budget).
    """

    def _one(member: CouncilMember) -> JudgeResult:
        try:
            text, usage = call_one_judge(member.model, system, build_user(member.model))
        except Exception as err:  # noqa: BLE001 — member failure must not abort the council
            return JudgeResult(member.family, member.model, ok=False, error=str(err)[:400])
        try:
            verdicts = parse_verdict_text(text, n_criteria)
        except ValueError as err:
            return JudgeResult(
                member.family,
                member.model,
                ok=False,
                usage=usage,
                error=f"parse: {err}",
                raw_response=text[:2000],
            )
        return JudgeResult(
            member.family, member.model, ok=True, verdicts=verdicts, usage=usage
        )

    with ThreadPoolExecutor(max_workers=max(1, len(members))) as pool:
        return list(pool.map(_one, members))


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #


def grade_council(
    rubrics: list[dict],
    task_description: str,
    transcript: str,
    workspace_state: str,
    verifier_output: str,
    members: list[CouncilMember] | None = None,
) -> dict:
    """Grade a rubric with the council. Always returns a scores dict.

    On total council failure every criterion abstains and ``overall_score`` is
    0.0 — there is no single-judge fallback, by design: a lone surviving judge
    is not a council and should not be reported as one.
    """
    members = members or council_members()
    system = _judge_system_prompt()
    raw_evidence_chars = len(transcript) + len(workspace_state) + len(verifier_output)
    budgets: dict[str, int | None] = {}

    def build_user(model: str) -> str:
        budget = member_evidence_budget(model)
        budgets[model] = budget
        fitted = fit_evidence(transcript, workspace_state, verifier_output, budget)
        if budget is not None and raw_evidence_chars > budget:
            logger.info(
                "evidence trimmed for %s: %d -> %d chars (budget %d)",
                model, raw_evidence_chars, sum(len(b) for b in fitted), budget,
            )
        return _judge_user_prompt(task_description, rubrics, *fitted)

    results = _run_council(members, system, build_user, len(rubrics))
    # Keyed by FAMILY, not model: a roster may legitimately seat the same model
    # twice (or an operator may pin one during an outage), and keying by model
    # would collapse those seats into one vote.
    surviving = {r.family: r.verdicts for r in results if r.ok}

    # The primary is the first roster entry — tiebreaker and source of truth.
    primary_idx = 0 if members else None
    primary_model = members[primary_idx].model if primary_idx is not None else None

    n_members = len(members)
    crit_out: list[dict] = []
    truncation_flags: list[int] = []
    abstention_flags: list[int] = []
    weighted = 0.0
    passed = 0

    # Denominator: sum of POSITIVE weights only. Using all weights overshoots 1.0
    # whenever penalties exist.
    total_w = sum(
        _extract_weight(r) for r in rubrics if isinstance(r, dict) and _extract_weight(r) > 0
    ) or 1.0

    for i, r in enumerate(rubrics):
        wt = _extract_weight(r) if isinstance(r, dict) else 1.0
        per_satisfied: list[bool] = []
        per_rationale: list[str] = []
        per_truncation: list[bool] = []
        per_voted: list[bool] = []

        for m in members:
            vs = surviving.get(m.family)
            if vs is None:
                per_voted.append(False)
                per_satisfied.append(False)
                per_rationale.append("(abstained — judge call failed)")
                per_truncation.append(False)
            elif i < len(vs):
                v = vs[i]
                per_voted.append(True)
                per_satisfied.append(bool(v.get("satisfied", False)))
                per_rationale.append(str(v.get("rationale", "") or ""))
                per_truncation.append(bool(v.get("truncation_affected", False)))
            else:
                per_voted.append(False)
                per_satisfied.append(False)
                per_rationale.append("(abstained — output truncated before this criterion)")
                per_truncation.append(False)

        voters = sum(1 for vd in per_voted if vd)
        full_coverage = voters == n_members
        yes_votes = sum(1 for s in per_satisfied if s)
        unanimous_yes = full_coverage and yes_votes == n_members
        unanimous_no = full_coverage and yes_votes == 0
        primary_voted = primary_idx is not None and per_voted[primary_idx]

        if unanimous_yes or unanimous_no:
            verdict_satisfied = unanimous_yes
            resolved_by = "unanimous"
            human_eval = ""
        elif primary_voted:
            verdict_satisfied = bool(per_satisfied[primary_idx])
            resolved_by = "primary"
            human_eval = ""
        else:
            abstention_flags.append(i)
            verdict_satisfied = False
            resolved_by = "human_eval"
            human_eval = "required"

        if resolved_by == "human_eval":
            criterion_passed = False
        else:
            criterion_passed = _criterion_pass_from_satisfied(verdict_satisfied, wt)
            if criterion_passed:
                passed += 1
            # A positive criterion contributes its weight only when satisfied; a
            # negative criterion that is satisfied subtracts its weight.
            if verdict_satisfied:
                weighted += wt

        if any(per_truncation):
            truncation_flags.append(i)

        crit_out.append(
            {
                "id": i,
                "number": r.get("number", f"R{i + 1}") if isinstance(r, dict) else f"R{i + 1}",
                "weight": wt,
                "satisfied": verdict_satisfied,
                "passed": criterion_passed,
                "resolved_by": resolved_by,
                "human_eval": human_eval,
                "justification": _governing_rationale(
                    resolved_by, primary_idx, per_rationale, per_voted
                ),
                "voters": voters,
                "criterion": r.get("criterion") if isinstance(r, dict) else str(r),
                "votes": "/".join(
                    ("Yes" if s else "No") if vd else "Abstain"
                    for s, vd in zip(per_satisfied, per_voted)
                ),
                "satisfied_by_judge": per_satisfied,
                "voted_by_judge": per_voted,
                "rationales_by_judge": per_rationale,
                "truncation_affected_by_judge": per_truncation,
                "judges": [m.family for m in members],
                "is_positive": wt >= 0,
            }
        )

    # final_reward = (Σ passed_positive_w − Σ |triggered_negative_w|) / Σ positive_w
    # Clamped to [0, 1]: the rubric must report on the same scale as the tests'
    # reward (scripts/score_reward.py clamps identically), and a negative score
    # propagates — into combined_score, into the bundle, into a model's mean.
    # overall_score_raw keeps the unclamped quotient so clamping the headline
    # number does not erase the signal that penalties overran what was earned.
    overall_raw = weighted / total_w
    overall = max(0.0, min(1.0, overall_raw))

    per_member: dict[str, dict] = {}
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "request_count": 0,
    }
    for res in results:
        u = res.usage or {}
        for k in totals:
            totals[k] += int(u.get(k, 0) or 0)
        entry = {
            "model": _display_model(res.model),
            "ok": res.ok,
            **{k: int(u.get(k, 0) or 0) for k in totals},
        }
        if res.error:
            entry["error"] = res.error[:200]
        budget = budgets.get(res.model)
        entry["evidence_budget_chars"] = budget
        entry["evidence_trimmed"] = budget is not None and raw_evidence_chars > budget
        per_member[res.family] = entry

    return {
        "overall_score": round(overall, 6),
        "overall_score_raw": round(overall_raw, 6),
        "criteria_total": len(rubrics),
        "criteria_passed": passed,
        "criteria_abstained": len(abstention_flags),
        "weighted_points": round(weighted, 6),
        "positive_weight_total": round(total_w, 6),
        "abstention_flags": abstention_flags,
        "truncation_flags": truncation_flags,
        "criteria": crit_out,
        "judge_council": {
            "members": [m.family for m in members],
            "models": {m.family: _display_model(m.model) for m in members},
            "primary": members[primary_idx].family if primary_idx is not None else None,
            "surviving": [res.family for res in results if res.ok],
            "usage": totals,
            "per_member": per_member,
            "prompt_versions": prompt_versions(),
            "graded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }
