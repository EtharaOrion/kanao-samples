#!/usr/bin/env python3
"""Authoring-time QC for a Kanao task directory.

Checks the five artifacts against each other -- instruction.md, tests/rubric.json,
tests/test_outputs.py + tests/test_weights.json, environment/, solution/TRUTH.md --
and reports every machine-decidable inconsistency.

Everything here is deterministic. Judgement calls (is this rubric criterion actually
implied by the instruction? is this the same behaviour scored twice?) are not guessed
at: they are emitted as pre-computed adjudication worksheets so a reviewer decides
without having to search the tree. That split is what keeps a review inside 20 minutes.

Usage:
    python3 validate_task.py <task_dir>              # markdown report to stdout
    python3 validate_task.py <task_dir> --json       # machine-readable, for forge
    python3 validate_task.py <task_dir> --quiet      # findings only, no worksheets

Exit codes:
    0  no BLOCKER and no ERROR findings
    1  at least one ERROR (task is inconsistent; fix before shipping)
    2  at least one BLOCKER (task cannot be scored as written)
    3  the task directory itself is unusable (missing/unparseable required files)
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# contract constants -- change these only when the Kanao task contract changes
# --------------------------------------------------------------------------- #

ALLOWED_WEIGHTS = {1, 3, 5, -1, -3, -5}

# importance label -> the single |weight| it is allowed to carry
IMPORTANCE_MAGNITUDE = {
    "critically_important": 5,
    "important": 3,
    "minor": 1,
    "nice_to_have": 1,
}

REQUIRED_RUBRIC_KEYS = (
    "number",
    "criterion",
    "is_positive",
    "type",
    "evaluation_target",
    "importance",
    "weight",
)

CANONICAL_EVALUATION_TARGETS = {"trajectory", "state_change"}

EVALUATION_TARGET_ALIASES = {
    "transcript": "trajectory",
    "workspace_state": "state_change",
    "env_state": "state_change",
}

OUTPUT_VERB_RE = re.compile(
    r"\b(?:writ(?:e|es|ing|ten)|creat(?:e|es|ing|ed)|produc(?:e|es|ing|ed)|emit(?:s|ting|ted)?"
    r"|generat(?:e|es|ing|ed)|output(?:s|ting)?|sav(?:e|es|ing|ed)|record(?:s|ing|ed)?"
    r"|append(?:s|ing|ed)?|land(?:s|ing|ed)?)\b",
    re.I,
)

VALID_DIFFICULTY = {"trivial", "easy", "medium", "hard", "expert"}

# tests whose weight must not land in the `available` denominator
NON_SCORING_TEST_PREFIXES = ("test_infra_", "test_gate_")

# share of total scorable weight the rubric lane may own, from
# Old_format/TRAJECTORY_QC_CHECKLIST.md section 4
RUBRIC_SHARE_BAND = (0.20, 0.75)

REQUIRED_FILES = (
    "task.toml",
    "instruction.md",
    "tests/rubric.json",
    "tests/test_outputs.py",
    "tests/test_weights.json",
    "tests/test.sh",
    "solution/TRUTH.md",
)

# Phrases that mark a requirement as high-priority in instruction.md prose.
# Matched case-insensitively against single sentences.
CRITICAL_MARKERS = (
    "must",
    "must not",
    "critical",
    "critically",
    "mandatory",
    "required",
    "do not",
    "never",
    "leave all three files",
    "byte-for-byte",
    "unchanged",
    "not in question",
    "is not optional",
    "under no circumstances",
    "forbidden",
    "not available to you",
)

# Phrases that promise the agent freedom. If a detector or a hash pin contradicts
# one of these, the task punishes behaviour it told the agent was allowed --
# the 58081829 defect class.
PERMISSION_MARKERS = (
    "are authorized",
    "is authorized",
    "you may",
    "you can",
    "are allowed",
    "is allowed",
    "feel free",
    "at your discretion",
    "any approach",
    "unusual route",
    "no particular",
)

# tokens too common to carry grounding signal
STOPWORDS = frozenset("""
a an and are as at be been but by can cannot did do does doing done for from had has have
how if in into is it its must no not of on once only or over should so than that the their
them then there these they this those to under until up use used uses using was were what
when where which while will with within without you your yours able after again all also
always any because before both during each either else enough even ever every few first
here just least less like made make many more most much neither new next none nor now
other others own per rather same second since some such take taken takes thing things
three two very via well whether whose why would
task tasks file files change changes changed agent run runs test tests
""".split())

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-/]{2,}")

# a path-ish token: has a slash, or a bare filename with a known extension
PATHY_RE = re.compile(
    r"[A-Za-z0-9_.\-/]*(?:/[A-Za-z0-9_.\-]+)+|"
    r"[A-Za-z0-9_.\-]+\.(?:py|json|tf|sh|md|ya?ml|toml|txt|cfg|ini|conf|sql|service|env)\b"
)

KNOWN_EXT_RE = re.compile(
    r"\.(?:py|json|tf|tfvars|sh|bash|md|ya?ml|toml|txt|cfg|ini|conf|sql|service|socket|timer"
    r"|env|lock|cast|xml|html|csv|tsv|log|pem|key|crt|j2|tmpl|rules|service|Dockerfile)$",
    re.I,
)

# Tokens that look like paths but address something other than the filesystem:
# cloud resource IDs, ARM/provider aliases, system paths no task owns.
NON_FS_PATH_RE = re.compile(
    r"^/(?:bin|sbin|usr|lib|lib64|etc|proc|sys|dev|boot|opt/homebrew)(?:/|$)"
    r"|^/(?:subscriptions|providers|resourceGroups|resourcegroups)/"
    r"|(?:^|/)(?:Microsoft|Azure|Google|Alibaba|AWS)\.[A-Za-z]"
    r"|^(?:arn|urn|gs|s3|az|http|https|ssh|git):"
    r"|::",
)

SEVERITY_ORDER = {"BLOCKER": 0, "ERROR": 1, "WARN": 2, "INFO": 3}


# --------------------------------------------------------------------------- #
# finding model
# --------------------------------------------------------------------------- #


@dataclass
class Finding:
    """One machine-decided defect."""

    code: str
    severity: str  # BLOCKER | ERROR | WARN | INFO
    section: str  # which report section it belongs to
    locus: str  # exact file / criterion / test it concerns
    problem: str
    why: str
    impact: str
    fix: str

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "section": self.section,
            "locus": self.locus,
            "problem": self.problem,
            "why": self.why,
            "impact": self.impact,
            "fix": self.fix,
        }


@dataclass
class Task:
    """Parsed task artifacts. Fields are empty when the artifact is unusable."""

    root: Path
    toml: dict = field(default_factory=dict)
    instruction: str = ""
    rubric: list = field(default_factory=list)
    weights: dict = field(default_factory=dict)
    test_src: str = ""
    test_fns: list = field(default_factory=list)  # [(name, lineno, docstring, body_src)]
    truth: str = ""
    env_paths: set = field(default_factory=set)  # repo-relative strings under environment/
    env_blobs: dict = field(default_factory=dict)  # relpath -> text, for small text files
    fatal: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def load_task(root: Path) -> Task:
    t = Task(root=root)

    for rel in REQUIRED_FILES:
        if not (root / rel).exists():
            t.fatal.append(f"missing required file: {rel}")

    raw = _read(root / "task.toml")
    if raw is not None:
        try:
            t.toml = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            t.fatal.append(f"task.toml does not parse: {exc}")

    t.instruction = _read(root / "instruction.md") or ""
    t.truth = _read(root / "solution" / "TRUTH.md") or ""

    raw = _read(root / "tests" / "rubric.json")
    if raw is not None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            t.fatal.append(f"tests/rubric.json does not parse: {exc}")
        else:
            if isinstance(parsed, dict):  # tolerate {"criteria": [...]}
                parsed = parsed.get("criteria", parsed.get("rubric", []))
            if not isinstance(parsed, list):
                t.fatal.append("tests/rubric.json is not a list of criteria")
            else:
                t.rubric = parsed

    raw = _read(root / "tests" / "test_weights.json")
    if raw is not None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            t.fatal.append(f"tests/test_weights.json does not parse: {exc}")
        else:
            if isinstance(parsed, dict):
                t.weights = parsed
            else:
                t.fatal.append("tests/test_weights.json is not an object")

    t.test_src = _read(root / "tests" / "test_outputs.py") or ""
    if t.test_src:
        t.test_fns = parse_test_functions(t.test_src)

    env = root / "environment"
    if env.is_dir():
        for p in env.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(env).as_posix()
            t.env_paths.add(rel)
            try:
                if p.stat().st_size <= 262_144:
                    t.env_blobs[rel] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

    return t


def parse_test_functions(src: str) -> list:
    """Return [(name, lineno, docstring, source_segment)] for every module-level test_*."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out.append(
                (
                    node.name,
                    node.lineno,
                    ast.get_docstring(node) or "",
                    ast.get_source_segment(src, node) or "",
                )
            )
    return out


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #


def sentences(text: str) -> list:
    """Split prose into sentences, carrying the 1-based line number of each."""
    out = []
    line_no = 1
    buf: list[str] = []
    buf_line = 1
    for ch in text:
        if not buf:
            buf_line = line_no
        buf.append(ch)
        if ch == "\n":
            line_no += 1
        if ch in ".!?" or (ch == "\n" and "".join(buf).strip().endswith(":")):
            s = "".join(buf).strip()
            if len(s) > 12:
                out.append((buf_line, re.sub(r"\s+", " ", s)))
                buf = []
    s = "".join(buf).strip()
    if len(s) > 12:
        out.append((buf_line, re.sub(r"\s+", " ", s)))
    return out


def tokens(text: str) -> set:
    """Content tokens, lowercased, stopwords and short words dropped."""
    found = {m.group(0).lower().strip("./-") for m in WORD_RE.finditer(text)}
    return {w for w in found if w and w not in STOPWORDS and len(w) > 2}


def paths_in(text: str) -> set:
    """Filesystem-path-looking tokens, normalised, with non-filesystem tokens dropped."""
    out = set()
    for m in PATHY_RE.finditer(text):
        tok = m.group(0).strip("`'\"(),;:").rstrip(".")
        if not tok or tok.startswith(("http", "git@")):
            continue
        if re.fullmatch(r"[\d./]+", tok):
            continue
        if NON_FS_PATH_RE.search(tok):
            continue
        out.add(tok)
    return out


def looks_like_real_file(t: Task, candidate: str) -> bool:
    """True when the token is concrete enough that 'it does not exist' is actionable."""
    c = norm_path(candidate)
    if not c:
        return False
    if KNOWN_EXT_RE.search(c):
        return True
    parent = c.rsplit("/", 1)[0] if "/" in c else ""
    return bool(parent) and env_has(t, parent)


def module_level_helpers(src: str) -> set:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("test")
    }


def called_helpers(src: str, helpers: set) -> set:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    return {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in helpers
    }


def norm_path(p: str) -> str:
    """Strip container prefixes so instruction/test/env paths compare equal."""
    p = p.strip().strip("`'\"").lstrip("./")
    for prefix in ("app/workspace/", "workspace/", "app/", "logs/", "srv/"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
    return p.rstrip("/")


def env_has(t: Task, candidate: str) -> bool:
    """Does `candidate` name something that exists under environment/ ?"""
    c = norm_path(candidate)
    if not c:
        return False
    for rel in t.env_paths:
        r = norm_path(rel)
        if r == c or r.endswith("/" + c) or c.endswith("/" + r):
            return True
        # directory reference
        if r.startswith(c + "/"):
            return True
    return False


def mentioned_anywhere(needle: str, *haystacks: str) -> bool:
    n = norm_path(needle).lower()
    if not n:
        return False
    tail = n.rsplit("/", 1)[-1]
    for h in haystacks:
        low = h.lower()
        if n in low or (len(tail) > 3 and tail in low):
            return True
    return False


def classify_instruction_paths(t: Task) -> tuple:
    """Split paths named in instruction.md into inputs the env must provide and
    artifacts the agent is told to produce.

    A path is an output when an output verb appears in its own sentence or in the two
    sentences before it -- instructions routinely read "Write the results to:" followed
    by the path alone on its own line.
    """
    sents = sentences(t.instruction)
    inputs: set[str] = set()
    outputs: set[str] = set()
    for i, (_ln, s) in enumerate(sents):
        found = paths_in(s)
        if not found:
            continue
        window = " ".join(x for _l, x in sents[max(0, i - 2) : i + 1])
        bucket = outputs if OUTPUT_VERB_RE.search(window) else inputs
        bucket |= found
    return inputs - outputs, outputs


def is_critical_sentence(s: str) -> bool:
    low = s.lower()
    return any(m in low for m in CRITICAL_MARKERS)


def overlap(a: set, b: set) -> float:
    """Jaccard-ish containment: how much of the smaller set lives in the larger."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


# --------------------------------------------------------------------------- #
# section 0 -- schema and contract
# --------------------------------------------------------------------------- #


def check_schema(t: Task) -> list:
    f: list[Finding] = []
    meta = t.toml.get("metadata", {})
    task = t.toml.get("task", {})

    uuid = str(task.get("uuid_v5") or task.get("uuid") or "")
    if uuid and uuid != t.root.name:
        f.append(
            Finding(
                "SCHEMA_UUID_DIR_MISMATCH",
                "ERROR",
                "0. Schema & contract",
                f"task.toml [task].uuid_v5 = {uuid!r} vs directory {t.root.name!r}",
                "The declared uuid does not match the directory it lives in.",
                "Harbor keys trajectories and registry entries by uuid; a mismatch silently "
                "orphans results from the task.",
                "No impact on scoring arithmetic, but results cannot be traced back to the task.",
                "Rename the directory to the uuid, or regenerate uuid_v5 from the task name.",
            )
        )

    diff = str(meta.get("difficulty", "")).lower()
    if diff and diff not in VALID_DIFFICULTY:
        f.append(
            Finding(
                "SCHEMA_DIFFICULTY_VOCAB",
                "ERROR",
                "0. Schema & contract",
                f"task.toml [metadata].difficulty = {diff!r}",
                f"Not one of {sorted(VALID_DIFFICULTY)}.",
                "Difficulty drives the expected pass@8 band used at trajectory QC.",
                "Calibration cannot be checked against an unknown tier.",
                "Set difficulty to one of the five known tiers.",
            )
        )

    if not task.get("authors"):
        f.append(
            Finding(
                "SCHEMA_NO_AUTHORS",
                "WARN",
                "0. Schema & contract",
                "task.toml [task].authors",
                "No authors recorded.",
                "Delivery requires an attributable author per task.",
                "Blocks delivery sign-off, not scoring.",
                "Add at least one {name, email} entry.",
            )
        )
    return f


# --------------------------------------------------------------------------- #
# section 2 -- rubric weights and critical requirements
# --------------------------------------------------------------------------- #


def check_rubric_weights(t: Task) -> list:
    f: list[Finding] = []
    seen_numbers: Counter = Counter()

    for i, c in enumerate(t.rubric):
        label = str(c.get("number") or f"index {i}")
        locus = f"tests/rubric.json :: {label}"
        seen_numbers[label] += 1

        missing = [k for k in REQUIRED_RUBRIC_KEYS if k not in c]
        if missing:
            f.append(
                Finding(
                    "RUBRIC_MISSING_KEYS",
                    "ERROR",
                    "2. Critical requirements (+5/-5)",
                    locus,
                    f"Missing required key(s): {', '.join(missing)}.",
                    "Every criterion must carry the full contract so the rubric lane is "
                    "mechanically reducible.",
                    "A criterion with no weight or no polarity cannot be scored at all.",
                    f"Add {', '.join(missing)} to {label}.",
                )
            )

        w = c.get("weight")
        if not isinstance(w, int) or isinstance(w, bool):
            f.append(
                Finding(
                    "RUBRIC_WEIGHT_NOT_INT",
                    "BLOCKER",
                    "2. Critical requirements (+5/-5)",
                    locus,
                    f"weight is {w!r} ({type(w).__name__}), not an integer.",
                    "Weights are summed as integers by the reducer.",
                    "Rubric score is not computable; the task cannot be scored as written.",
                    "Set weight to one of +1/+3/+5/-1/-3/-5.",
                )
            )
        elif w not in ALLOWED_WEIGHTS:
            f.append(
                Finding(
                    "RUBRIC_WEIGHT_OUT_OF_SET",
                    "ERROR",
                    "2. Critical requirements (+5/-5)",
                    locus,
                    f"weight = {w}, outside the allowed set {{+1,+3,+5,-1,-3,-5}}.",
                    "The contract fixes three magnitudes so importance is comparable "
                    "across tasks.",
                    "Distorts the rubric share of total reward and breaks cross-task calibration.",
                    f"Re-weight {label} to the nearest allowed magnitude that matches its importance.",
                )
            )

        pos = c.get("is_positive")
        if isinstance(w, int) and not isinstance(w, bool) and isinstance(pos, bool):
            if pos and w < 0:
                f.append(
                    Finding(
                        "RUBRIC_POLARITY_CONFLICT",
                        "ERROR",
                        "2. Critical requirements (+5/-5)",
                        locus,
                        f"is_positive=true but weight={w}.",
                        "Polarity and weight sign must agree.",
                        "A reducer reading either field alone computes a different score.",
                        f"Make the sign of weight agree with is_positive on {label}.",
                    )
                )
            if (not pos) and w > 0:
                f.append(
                    Finding(
                        "RUBRIC_POLARITY_CONFLICT",
                        "ERROR",
                        "2. Critical requirements (+5/-5)",
                        locus,
                        f"is_positive=false but weight={w}.",
                        "A penalty criterion carrying a positive weight rewards the "
                        "behaviour it was written to punish.",
                        "Agents are paid for violating the constraint.",
                        f"Set weight to -{abs(w)} on {label}, or flip is_positive.",
                    )
                )

        imp = str(c.get("importance", "")).strip().lower()
        if imp and imp not in IMPORTANCE_MAGNITUDE:
            f.append(
                Finding(
                    "RUBRIC_IMPORTANCE_VOCAB",
                    "WARN",
                    "2. Critical requirements (+5/-5)",
                    locus,
                    f"importance = {imp!r}, not one of {sorted(IMPORTANCE_MAGNITUDE)}.",
                    "Importance is the human-readable half of the weight contract.",
                    "Weight/importance agreement cannot be checked.",
                    "Use a known importance label.",
                )
            )
        elif imp and isinstance(w, int) and not isinstance(w, bool):
            want = IMPORTANCE_MAGNITUDE[imp]
            if abs(w) != want:
                if imp == "critically_important":
                    sev = "ERROR"
                elif want == 3 and abs(w) == 1:
                    sev = "INFO"
                else:
                    sev = "WARN"
                f.append(
                    Finding(
                        "RUBRIC_IMPORTANCE_WEIGHT_MISMATCH",
                        sev,
                        "2. Critical requirements (+5/-5)",
                        locus,
                        f"importance={imp!r} implies |weight|={want} but weight={w}.",
                        "A criterion declared critical must carry the critical magnitude, "
                        "and a non-critical criterion must not borrow it.",
                        "Critical requirements under-penalised, or ordinary requirements "
                        "crowding out the ones that matter.",
                        f"Either set weight to {'+' if w >= 0 else '-'}{want} or "
                        f"downgrade importance on {label}.",
                    )
                )

        crit = str(c.get("criterion", "")).strip()
        if len(crit) < 25:
            f.append(
                Finding(
                    "RUBRIC_CRITERION_TOO_THIN",
                    "ERROR",
                    "2. Critical requirements (+5/-5)",
                    locus,
                    f"criterion text is {len(crit)} chars: {crit!r}",
                    "A criterion must state an observable condition precisely enough "
                    "that two evaluators agree.",
                    "Unreproducible human scoring; rubric lane becomes noise.",
                    "Rewrite as a single checkable condition naming the artifact and the "
                    "expected state.",
                )
            )

    targets = {str(c.get("evaluation_target", "")).strip() for c in t.rubric}
    targets.discard("")
    unknown = sorted(t for t in targets if t not in CANONICAL_EVALUATION_TARGETS
                     and t not in EVALUATION_TARGET_ALIASES)
    aliased = sorted(t for t in targets if t in EVALUATION_TARGET_ALIASES)
    if unknown:
        f.append(
            Finding(
                "RUBRIC_TARGET_VOCAB",
                "ERROR",
                "0. Schema & contract",
                "tests/rubric.json :: evaluation_target",
                f"Unrecognised evaluation_target value(s): {', '.join(unknown)}.",
                "The target tells the evaluator which artifact to score from.",
                "An evaluator cannot tell whether to read the transcript or the final "
                "workspace, so the same criterion gets scored differently by different people.",
                f"Map {', '.join(unknown)} onto 'trajectory' or 'state_change'.",
            )
        )
    if aliased:
        f.append(
            Finding(
                "RUBRIC_TARGET_NON_CANONICAL",
                "WARN",
                "0. Schema & contract",
                "tests/rubric.json :: evaluation_target",
                "Uses non-canonical synonym(s): "
                + ", ".join(f"{a} (= {EVALUATION_TARGET_ALIASES[a]})" for a in aliased),
                "Other tasks in the corpus spell these 'trajectory' and 'state_change'.",
                "Cross-task tooling that groups criteria by target silently splits them into "
                "separate buckets, so corpus-level rubric statistics are wrong.",
                "Rename to the canonical spelling for consistency across the corpus.",
            )
        )

    for label, n in seen_numbers.items():
        if n > 1:
            f.append(
                Finding(
                    "RUBRIC_DUPLICATE_NUMBER",
                    "ERROR",
                    "2. Critical requirements (+5/-5)",
                    f"tests/rubric.json :: {label}",
                    f"criterion number {label!r} appears {n} times.",
                    "Numbers are the join key between the rubric and the evaluator's "
                    "justification record.",
                    "Justifications cannot be matched to criteria; audit trail breaks.",
                    "Renumber so every criterion has a unique number.",
                )
            )
    return f


def check_critical_coverage(t: Task) -> list:
    """Instruction sentences flagged as high-priority with no +5/-5 anywhere."""
    f: list[Finding] = []
    crit_sentences = [(ln, s) for ln, s in sentences(t.instruction) if is_critical_sentence(s)]
    if not crit_sentences:
        return f

    strong = [c for c in t.rubric if abs(int(c.get("weight", 0) or 0)) == 5]
    strong_tests = [n for n, w in t.weights.items() if abs(int(w)) == 5]
    strong_text = " ".join(str(c.get("criterion", "")) for c in strong) + " " + " ".join(strong_tests)
    strong_tok = tokens(strong_text)

    for ln, s in crit_sentences:
        stok = tokens(s)
        if not stok:
            continue
        if overlap(stok, strong_tok) < 0.18:
            f.append(
                Finding(
                    "CRITICAL_NOT_WEIGHTED_5",
                    "WARN",
                    "2. Critical requirements (+5/-5)",
                    f"instruction.md:{ln}",
                    f"Emphasised requirement has no ±5 criterion or test covering it: {s[:170]!r}",
                    "instruction.md marks this as mandatory, but nothing in the rubric or "
                    "test weights carries critical magnitude for it.",
                    "A requirement the task calls mandatory can be violated at the cost of a "
                    "±1 or ±3, so the stated priority is not the scored priority.",
                    "Either add a ±5 criterion/test for this requirement, or soften the "
                    "language in instruction.md so the stated priority matches the scored one.",
                )
            )
    return f


# --------------------------------------------------------------------------- #
# section 3 -- pytest <-> weights <-> rubric
# --------------------------------------------------------------------------- #


def check_tests(t: Task) -> list:
    f: list[Finding] = []
    defined = {n for n, *_ in t.test_fns}
    weighted = set(t.weights)

    for name in sorted(defined - weighted):
        f.append(
            Finding(
                "TEST_UNWEIGHTED",
                "ERROR",
                "3. Pytest <-> rubrics overlap",
                f"tests/test_outputs.py :: {name}",
                "Test is defined but has no entry in test_weights.json.",
                "The scorer iterates test_weights.json; an unweighted test contributes nothing.",
                "The behaviour it checks is silently unscored -- a requirement the author "
                "believed was covered is not.",
                f"Add {name} to test_weights.json with the weight its requirement deserves, "
                "or delete the test.",
            )
        )

    for name in sorted(weighted - defined):
        f.append(
            Finding(
                "WEIGHT_ORPHAN",
                "BLOCKER",
                "3. Pytest <-> rubrics overlap",
                f"tests/test_weights.json :: {name}",
                "Weighted test name does not exist in test_outputs.py.",
                "The scorer cannot find a verdict for this name.",
                "Either the weight is dropped from `available` or the scorer records a "
                "missing verdict -- in both cases the reward denominator is wrong.",
                f"Rename the weight key to the real test function, or remove {name}.",
            )
        )

    for name, w in sorted(t.weights.items()):
        try:
            wi = int(w)
        except (TypeError, ValueError):
            f.append(
                Finding(
                    "WEIGHT_NOT_INT",
                    "BLOCKER",
                    "3. Pytest <-> rubrics overlap",
                    f"tests/test_weights.json :: {name}",
                    f"weight is {w!r}, not an integer.",
                    "Weights are summed as integers.",
                    "Reward is not computable.",
                    "Use an integer from {+1,+3,+5,-1,-3,-5}.",
                )
            )
            continue

        if wi not in ALLOWED_WEIGHTS:
            f.append(
                Finding(
                    "WEIGHT_OUT_OF_SET",
                    "ERROR",
                    "3. Pytest <-> rubrics overlap",
                    f"tests/test_weights.json :: {name}",
                    f"weight = {wi}, outside {{+1,+3,+5,-1,-3,-5}}.",
                    "Test weights use the same three magnitudes as the rubric.",
                    "Breaks cross-task comparability of reward.",
                    "Re-weight to the nearest allowed magnitude.",
                )
            )

        is_detector_name = name.startswith("test_negative_")
        if is_detector_name and wi > 0:
            f.append(
                Finding(
                    "DETECTOR_POSITIVE_WEIGHT",
                    "BLOCKER",
                    "3. Pytest <-> rubrics overlap",
                    f"tests/test_weights.json :: {name}",
                    f"test_negative_* detector carries weight +{wi}.",
                    "Detectors PASS when the agent did the forbidden thing, so a positive "
                    "weight pays for the violation.",
                    "The task rewards exactly the behaviour it forbids.",
                    f"Set {name} to -{abs(wi)}.",
                )
            )
        if (not is_detector_name) and wi < 0 and not name.startswith(NON_SCORING_TEST_PREFIXES):
            f.append(
                Finding(
                    "NEGATIVE_WEIGHT_NOT_DETECTOR",
                    "ERROR",
                    "3. Pytest <-> rubrics overlap",
                    f"tests/test_weights.json :: {name}",
                    f"Negative weight {wi} on a test not named test_negative_*.",
                    "The naming convention is how readers and the scorer tell detectors "
                    "from outcome tests.",
                    "A reviewer reading the suite mis-reads the polarity of the test.",
                    f"Rename to test_negative_{name.removeprefix('test_')}, or make the "
                    "weight positive.",
                )
            )

        if name.startswith(NON_SCORING_TEST_PREFIXES) and wi != 0:
            f.append(
                Finding(
                    "GATE_TEST_WEIGHTED",
                    "WARN",
                    "3. Pytest <-> rubrics overlap",
                    f"tests/test_weights.json :: {name}",
                    f"{name} carries weight {wi}.",
                    "test_gate_* / test_infra_* are mechanism markers and must be excluded "
                    "from the `available` denominator.",
                    "Double-counts the gate: once multiplicatively, once additively.",
                    f"Set {name} to 0 or drop it from test_weights.json.",
                )
            )

    # conjunctive all-or-nothing suite -- the e79449a4 defect class
    scoring = [
        (n, ln, doc, src)
        for n, ln, doc, src in t.test_fns
        if int(t.weights.get(n, 0) or 0) > 0
    ]
    if len(scoring) >= 3:
        helpers = module_level_helpers(t.test_src)
        shared = None
        for _n, _ln, _doc, src in scoring:
            calls = called_helpers(src, helpers)
            shared = calls if shared is None else (shared & calls)
        shared = shared or set()
        if shared:
            f.append(
                Finding(
                    "TESTS_SHARE_SINGLE_FIXTURE",
                    "WARN",
                    "3. Pytest <-> rubrics overlap",
                    "tests/test_outputs.py :: " + ", ".join(n for n, *_ in scoring),
                    "Every positively-weighted test routes through the same helper(s): "
                    + ", ".join(sorted(shared)),
                    "If that shared step raises or fails, all positive tests fail together "
                    "and the deterministic lane collapses to 0 regardless of partial progress.",
                    "Partial credit disappears; the rubric lane ends up owning 100% of the "
                    "reward, which is the e79449a4 defect signature.",
                    "Make each outcome test independently reachable, or prove the shared "
                    "helper cannot fail for a partially-correct submission.",
                )
            )
    return f


def check_overlap(t: Task) -> list:
    """Rubric criteria that restate a pytest assertion verbatim enough to double-count."""
    f: list[Finding] = []
    for name, _ln, doc, src in t.test_fns:
        w = int(t.weights.get(name, 0) or 0)
        if w == 0:
            continue
        # describe the test by its name, docstring, and the string literals it asserts on
        lits = " ".join(re.findall(r"[\"']([^\"'\n]{6,})[\"']", src))
        test_tok = tokens(name.replace("_", " ") + " " + doc + " " + lits)
        if len(test_tok) < 4:
            continue
        for c in t.rubric:
            cw = c.get("weight")
            if not isinstance(cw, int) or isinstance(cw, bool):
                continue
            ctok = tokens(str(c.get("criterion", "")))
            if len(ctok) < 4:
                continue
            ov = overlap(test_tok, ctok)
            if ov < 0.55:
                continue
            same_polarity = (w > 0) == (cw > 0)
            state_change = str(c.get("evaluation_target", "")) == "state_change"
            sev = "ERROR" if (same_polarity and state_change and ov >= 0.7) else "WARN"
            f.append(
                Finding(
                    "PYTEST_RUBRIC_DUPLICATION",
                    sev,
                    "3. Pytest <-> rubrics overlap",
                    f"{name} (w={w:+d})  <->  rubric {c.get('number')} (w={cw:+d})",
                    f"Token overlap {ov:.0%}; rubric target is {c.get('evaluation_target')!r}.",
                    "When a rubric criterion and a weighted test decide the same condition "
                    "from the same evidence, the requirement is paid twice.",
                    f"Up to {abs(w) + abs(cw)} weight hangs on one condition, inflating its "
                    "share of reward relative to equally important requirements.",
                    "Keep the deterministic test and re-aim the rubric criterion at the "
                    "behavioural half (how the agent reached it, what it reported), or drop "
                    "the rubric criterion.",
                )
            )
    return f


def check_lane_balance(t: Task) -> list:
    f: list[Finding] = []
    avail_tests = sum(
        int(w) for n, w in t.weights.items()
        if int(w or 0) > 0 and not n.startswith(NON_SCORING_TEST_PREFIXES)
    )
    avail_rubric = sum(
        int(c["weight"]) for c in t.rubric
        if isinstance(c.get("weight"), int) and not isinstance(c.get("weight"), bool) and c["weight"] > 0
    )
    total = avail_tests + avail_rubric
    if total == 0:
        f.append(
            Finding(
                "NO_POSITIVE_WEIGHT",
                "BLOCKER",
                "6. Complete coverage",
                "tests/test_weights.json + tests/rubric.json",
                "No positive weight anywhere.",
                "Nothing rewards a correct solution.",
                "Every submission scores 0; the task is unusable as a training signal.",
                "Add positively-weighted outcome tests and rubric criteria.",
            )
        )
        return f

    share = avail_rubric / total
    lo, hi = RUBRIC_SHARE_BAND
    if not (lo <= share <= hi):
        f.append(
            Finding(
                "RUBRIC_SHARE_OUT_OF_BAND",
                "ERROR",
                "6. Complete coverage",
                f"rubric share = {avail_rubric}/{total} = {share:.0%}",
                f"Outside the required band {lo:.0%}-{hi:.0%}.",
                "The deterministic lane must carry the bulk of the signal, and the rubric "
                "lane must not be vestigial either.",
                "Too high: reward is mostly subjective and unreproducible. Too low: the "
                "behavioural requirements in instruction.md are effectively unscored.",
                "Re-balance by moving weight between the two lanes until the share lands "
                f"inside {lo:.0%}-{hi:.0%}.",
            )
        )

    detectors = sum(
        abs(int(w)) for n, w in t.weights.items() if int(w or 0) < 0
    ) + sum(
        abs(int(c["weight"])) for c in t.rubric
        if isinstance(c.get("weight"), int) and not isinstance(c.get("weight"), bool) and c["weight"] < 0
    )
    if detectors > total:
        f.append(
            Finding(
                "PENALTY_EXCEEDS_REWARD",
                "ERROR",
                "6. Complete coverage",
                f"penalty weight {detectors} vs available {total}",
                "Total penalty exceeds total available credit.",
                "A single unlucky detector can drive reward to the floor regardless of how "
                "much of the task was solved.",
                "Reward saturates at 0 and stops discriminating between submissions.",
                "Reduce detector magnitudes or add positive weight for the work the task "
                "actually asks for.",
            )
        )
    return f


# --------------------------------------------------------------------------- #
# section 4 -- instruction + environment <-> truth
# --------------------------------------------------------------------------- #


def check_environment(t: Task) -> list:
    f: list[Finding] = []
    env = t.root / "environment"
    if not env.is_dir():
        f.append(
            Finding(
                "ENV_MISSING",
                "BLOCKER",
                "4. Instruction + environment <-> Truth",
                "environment/",
                "No environment/ directory.",
                "The agent has nothing to act on.",
                "Task cannot be run at all.",
                "Add the environment tree the instruction describes.",
            )
        )
        return f

    if not any(p.startswith("workspace/") for p in t.env_paths):
        f.append(
            Finding(
                "ENV_NO_WORKSPACE",
                "WARN",
                "4. Instruction + environment <-> Truth",
                "environment/",
                "No workspace/ subtree under environment/.",
                "Tasks mount the agent's working tree from environment/workspace.",
                "Agent may start in an empty or unexpected working directory.",
                "Confirm the Dockerfile/compose copies the intended tree, or add workspace/.",
            )
        )

    solve = _read(t.root / "solution" / "solve.sh") or ""
    produced = " ".join([solve, t.truth])
    env_text = " ".join(t.env_blobs.values())
    inputs, outputs = classify_instruction_paths(t)

    for p in sorted(inputs):
        if env_has(t, p) or mentioned_anywhere(p, env_text):
            continue
        f.append(
            Finding(
                "INSTRUCTION_PATH_NOT_IN_ENV",
                "ERROR",
                "4. Instruction + environment <-> Truth",
                f"instruction.md -> {p}",
                "Path the instruction tells the agent to read does not exist under "
                "environment/ and nothing in the environment would create it.",
                "The instruction sends the agent to a file the environment does not provide.",
                "The agent burns budget hunting a file that is not there, or invents one and "
                "then fails tests that expect the real thing.",
                f"Add {p} to the environment, or correct the path in instruction.md.",
            )
        )

    for p in sorted(outputs):
        if mentioned_anywhere(p, produced) or mentioned_anywhere(p, t.test_src):
            continue
        f.append(
            Finding(
                "INSTRUCTION_OUTPUT_UNVERIFIED",
                "ERROR",
                "4. Instruction + environment <-> Truth",
                f"instruction.md -> {p}",
                "The instruction tells the agent to write this artifact, but neither "
                "solution/solve.sh, TRUTH.md, nor any test mentions it.",
                "A required output that nothing produces in the reference solution and "
                "nothing reads back is an unverifiable requirement.",
                "Agents are asked for work that carries no credit, and a submission that "
                "omits it is indistinguishable from one that provides it.",
                f"Either produce and assert on {p}, or stop requiring it in instruction.md.",
            )
        )
    for p in sorted(paths_in(t.test_src)):
        if env_has(t, p) or mentioned_anywhere(p, produced):
            continue
        if norm_path(p).startswith(("tests/", "logs/", "tmp/", "var/")):
            continue
        if p.endswith((".py", ".pyc")) and "/" not in p:
            continue
        if not looks_like_real_file(t, p):
            continue
        f.append(
            Finding(
                "TEST_PATH_UNGROUNDED",
                "WARN",
                "4. Instruction + environment <-> Truth",
                f"tests/test_outputs.py -> {p}",
                "Test references a path that is neither in the environment nor created by "
                "solution/solve.sh or described in TRUTH.md.",
                "A test asserting on a path nothing produces can only fail.",
                "Dead weight in the denominator: the test can never pass, so its weight is "
                "unreachable credit.",
                f"Ground {p} in the environment or the reference solution, or drop the assertion.",
            )
        )

    # permission <-> detector contradiction (the 58081829 defect class)
    perms = [(ln, s) for ln, s in sentences(t.instruction)
             if any(m in s.lower() for m in PERMISSION_MARKERS)]
    pin_files = sorted(
        p for p in (t.root / "tests").rglob("*")
        if p.is_file() and re.search(r"hash|sha|pin|checksum|digest", p.name, re.I)
    )
    if perms and pin_files:
        for pf in pin_files:
            pinned = paths_in(_read(pf) or "")
            for ln, s in perms:
                granted = paths_in(s)
                hits = sorted(
                    q for q in pinned
                    if any(norm_path(q).endswith(norm_path(g).rsplit("/", 1)[-1])
                           or norm_path(g).rstrip("/") and norm_path(q).startswith(norm_path(g).rstrip("/") + "/")
                           for g in granted)
                )
                if hits:
                    f.append(
                        Finding(
                            "PERMISSION_PINNED_CONTRADICTION",
                            "ERROR",
                            "4. Instruction + environment <-> Truth",
                            f"instruction.md:{ln}  <->  {pf.relative_to(t.root).as_posix()}",
                            f"Instruction grants latitude ({s[:120]!r}) over paths that the "
                            f"grader pins: {', '.join(hits[:5])}.",
                            "The task tells the agent a change is permitted and then penalises "
                            "it through a content pin.",
                            "An agent that follows the instruction loses reward. This is the "
                            "58081829 defect signature.",
                            "Either narrow the permission sentence to exclude the pinned paths, "
                            "or stop pinning what the instruction authorises.",
                        )
                    )
    return f


def check_truth(t: Task) -> list:
    f: list[Finding] = []
    if len(t.truth.strip()) < 400:
        f.append(
            Finding(
                "TRUTH_TOO_THIN",
                "ERROR",
                "5. Rubrics <-> Truth",
                "solution/TRUTH.md",
                f"TRUTH.md is only {len(t.truth.strip())} chars.",
                "TRUTH.md is the evidence base every rubric criterion is scored against.",
                "Evaluators have nothing to ground scoring in; the rubric lane becomes opinion.",
                "Document the defect, the root cause, the load-bearing work and how each "
                "outcome is verified.",
            )
        )

    solve = _read(t.root / "solution" / "solve.sh") or ""
    haystacks = (t.truth, solve)

    for c in t.rubric:
        label = str(c.get("number") or "?")
        crit = str(c.get("criterion", ""))
        cpaths = {p for p in paths_in(crit) if "/" in p or "." in p}
        ungrounded = sorted(
            p for p in cpaths
            if not mentioned_anywhere(p, *haystacks) and not env_has(t, p)
        )
        if ungrounded:
            f.append(
                Finding(
                    "RUBRIC_PATH_NOT_IN_TRUTH",
                    "ERROR",
                    "5. Rubrics <-> Truth",
                    f"tests/rubric.json :: {label}",
                    "Criterion names artifact(s) absent from TRUTH.md, solve.sh and the "
                    f"environment: {', '.join(ungrounded)}.",
                    "A criterion must be provable from the reference solution.",
                    "The criterion cannot be adjudicated consistently; scores diverge between "
                    "evaluators.",
                    f"Either document {', '.join(ungrounded)} in TRUTH.md, or remove it from {label}.",
                )
            )

    # instruction grounding: a criterion whose vocabulary appears nowhere in the instruction
    itok = tokens(t.instruction)
    for c in t.rubric:
        label = str(c.get("number") or "?")
        ctok = tokens(str(c.get("criterion", "")))
        if len(ctok) < 5:
            continue
        if overlap(ctok, itok) < 0.12:
            f.append(
                Finding(
                    "RUBRIC_NOT_IN_INSTRUCTION",
                    "WARN",
                    "1. Instruction -> rubrics gaps",
                    f"tests/rubric.json :: {label} (w={c.get('weight')})",
                    f"Almost no vocabulary shared with instruction.md: "
                    f"{str(c.get('criterion',''))[:150]!r}",
                    "Candidate hidden requirement: the criterion may be scoring something "
                    "the agent was never told to do.",
                    "Agents are penalised for omitting work the instruction never asked for, "
                    "which makes the task unfair and the reward noisy.",
                    "Confirm the requirement is at least implicitly necessary to accomplish "
                    f"the task. If it is not, delete {label}. If it is, say so in instruction.md.",
                )
            )
    return f


# --------------------------------------------------------------------------- #
# worksheets -- pre-computed adjudication, not verdicts
# --------------------------------------------------------------------------- #


def build_worksheets(t: Task) -> dict:
    itok = tokens(t.instruction)
    rub_tok = {str(c.get("number")): tokens(str(c.get("criterion", ""))) for c in t.rubric}
    test_tok = {}
    for name, _ln, doc, src in t.test_fns:
        lits = " ".join(re.findall(r"[\"']([^\"'\n]{6,})[\"']", src))
        test_tok[name] = tokens(name.replace("_", " ") + " " + doc + " " + lits)

    # 1. instruction requirement -> who covers it
    reqs = []
    for ln, s in sentences(t.instruction):
        stok = tokens(s)
        if len(stok) < 4:
            continue
        rhits = sorted(
            (round(overlap(stok, rt), 2), num) for num, rt in rub_tok.items() if overlap(stok, rt) >= 0.2
        )
        thits = sorted(
            (round(overlap(stok, tt), 2), nm) for nm, tt in test_tok.items() if overlap(stok, tt) >= 0.2
        )
        reqs.append(
            {
                "line": ln,
                "sentence": s[:220],
                "critical_language": is_critical_sentence(s),
                "rubric": [n for _, n in sorted(rhits, reverse=True)[:4]],
                "tests": [n for _, n in sorted(thits, reverse=True)[:4]],
            }
        )

    uncovered = [
        r for r in reqs
        if not r["rubric"] and not r["tests"] and (r["critical_language"] or len(r["sentence"]) > 60)
    ]

    # 2. weight ledger
    ledger = {
        "tests_available": sum(
            int(w) for n, w in t.weights.items()
            if int(w or 0) > 0 and not n.startswith(NON_SCORING_TEST_PREFIXES)
        ),
        "tests_penalty": sum(abs(int(w)) for w in t.weights.values() if int(w or 0) < 0),
        "rubric_available": sum(
            int(c["weight"]) for c in t.rubric
            if isinstance(c.get("weight"), int) and not isinstance(c.get("weight"), bool) and c["weight"] > 0
        ),
        "rubric_penalty": sum(
            abs(int(c["weight"])) for c in t.rubric
            if isinstance(c.get("weight"), int) and not isinstance(c.get("weight"), bool) and c["weight"] < 0
        ),
        "rubric_criteria": len(t.rubric),
        "tests_defined": len(t.test_fns),
        "importance_histogram": dict(
            Counter(str(c.get("importance", "?")) for c in t.rubric)
        ),
        "rubric_weight_histogram": dict(
            Counter(c.get("weight") for c in t.rubric)
        ),
        "test_weight_histogram": dict(Counter(int(w or 0) for w in t.weights.values())),
    }
    tot = ledger["tests_available"] + ledger["rubric_available"]
    ledger["rubric_share"] = round(ledger["rubric_available"] / tot, 3) if tot else None

    # 3. constraint ledger -- "leave unchanged" sentences and what enforces them
    constraints = []
    for ln, s in sentences(t.instruction):
        low = s.lower()
        if not any(k in low for k in ("byte-for-byte", "unchanged", "leave ", "not in question",
                                      "do not", "must not", "not available", "forbidden")):
            continue
        cpaths = sorted(paths_in(s))
        enforcers = [
            n for n, tt in test_tok.items()
            if int(t.weights.get(n, 0) or 0) < 0 and (
                overlap(tokens(s), tt) >= 0.2
                or any(mentioned_anywhere(p, " ".join(
                    src for nm, _l, _d, src in t.test_fns if nm == n)) for p in cpaths)
            )
        ]
        rub_enforcers = [
            num for num, rt in rub_tok.items()
            if overlap(tokens(s), rt) >= 0.25
            and isinstance(next((c.get("weight") for c in t.rubric
                                 if str(c.get("number")) == num), None), int)
            and int(next(c.get("weight") for c in t.rubric if str(c.get("number")) == num)) < 0
        ]
        constraints.append(
            {
                "line": ln,
                "sentence": s[:220],
                "paths": cpaths,
                "detector_tests": enforcers,
                "penalty_rubrics": rub_enforcers,
                "unenforced": not enforcers and not rub_enforcers,
            }
        )

    # 4. rubric criteria with weak instruction grounding, ranked
    weak = sorted(
        (
            {
                "number": num,
                "weight": next((c.get("weight") for c in t.rubric if str(c.get("number")) == num), None),
                "overlap_with_instruction": round(overlap(rt, itok), 2),
                "criterion": next(
                    (str(c.get("criterion", ""))[:200] for c in t.rubric if str(c.get("number")) == num), ""
                ),
            }
            for num, rt in rub_tok.items()
            if len(rt) >= 5
        ),
        key=lambda d: d["overlap_with_instruction"],
    )[:10]

    return {
        "requirement_coverage": reqs,
        "uncovered_requirements": uncovered,
        "weight_ledger": ledger,
        "constraint_ledger": constraints,
        "weakest_instruction_grounding": weak,
    }


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

SECTIONS = (
    "0. Schema & contract",
    "1. Instruction -> rubrics gaps",
    "2. Critical requirements (+5/-5)",
    "3. Pytest <-> rubrics overlap",
    "4. Instruction + environment <-> Truth",
    "5. Rubrics <-> Truth",
    "6. Complete coverage",
)


def verdict_of(findings: list) -> tuple:
    counts = Counter(f.severity for f in findings)
    if counts["BLOCKER"]:
        return "BLOCKED", 2
    if counts["ERROR"]:
        return "CHANGES REQUIRED", 1
    if counts["WARN"]:
        return "PASS WITH NOTES", 0
    return "PASS", 0


def render_markdown(t: Task, findings: list, ws: dict, quiet: bool) -> str:
    verdict, _ = verdict_of(findings)
    counts = Counter(f.severity for f in findings)
    led = ws["weight_ledger"]
    out: list[str] = []
    A = out.append

    A(f"# Task QC -- `{t.root.name}`")
    A("")
    A(f"**Overall verdict: {verdict}**  "
      f"({counts['BLOCKER']} blocker, {counts['ERROR']} error, "
      f"{counts['WARN']} warn, {counts['INFO']} info)")
    A("")
    A(f"- rubric criteria: {led['rubric_criteria']} | pytest functions: {led['tests_defined']}")
    A(f"- available weight: tests {led['tests_available']} + rubric {led['rubric_available']} "
      f"= {led['tests_available'] + led['rubric_available']}")
    A(f"- penalty weight: tests -{led['tests_penalty']} + rubric -{led['rubric_penalty']}")
    share_txt = "n/a" if led["rubric_share"] is None else f"{led['rubric_share']:.0%}"
    A(f"- rubric share of available: {share_txt} "
      f"(band {RUBRIC_SHARE_BAND[0]:.0%}-{RUBRIC_SHARE_BAND[1]:.0%})")
    A("")

    unusable = any(f.code == "TASK_UNUSABLE" for f in findings)
    for sec in SECTIONS:
        items = [f for f in findings if f.section == sec]
        A(f"## {sec}")
        A("")
        if not items:
            A("Not evaluated: the task directory is unusable." if unusable
              else "No machine-decidable issues.")
            A("")
            continue
        items.sort(key=lambda f: SEVERITY_ORDER[f.severity])
        for f in items:
            A(f"### [{f.severity}] {f.code} -- `{f.locus}`")
            A("")
            A(f"- **Problem**: {f.problem}")
            A(f"- **Why it matters**: {f.why}")
            A(f"- **Impact**: {f.impact}")
            A(f"- **Fix**: {f.fix}")
            A("")

    if quiet:
        return "\n".join(out)

    A("## Adjudication worksheets")
    A("")
    A("Machine-computed candidates. These are *not* verdicts -- a reviewer decides each one. "
      "They exist so the decision needs no searching.")
    A("")

    unc = ws["uncovered_requirements"]
    A(f"### W1. Instruction sentences with no rubric and no test ({len(unc)})")
    A("")
    if not unc:
        A("None.")
    else:
        A("| line | critical? | sentence |")
        A("|---|---|---|")
        for r in unc[:25]:
            s = r["sentence"].replace("|", "\\|")
            A(f"| {r['line']} | {'YES' if r['critical_language'] else 'no'} | {s} |")
    A("")

    con = ws["constraint_ledger"]
    A(f"### W2. Constraint sentences and what enforces them ({len(con)})")
    A("")
    if not con:
        A("No constraint language found.")
    else:
        A("| line | paths | detectors | penalty rubrics | enforced? |")
        A("|---|---|---|---|---|")
        for c in con[:25]:
            A(f"| {c['line']} | {', '.join(c['paths'][:3]) or '-'} | "
              f"{', '.join(c['detector_tests'][:3]) or '-'} | "
              f"{', '.join(c['penalty_rubrics'][:3]) or '-'} | "
              f"{'NO' if c['unenforced'] else 'yes'} |")
    A("")

    weak = ws["weakest_instruction_grounding"]
    A("### W3. Rubric criteria ranked by weakest instruction grounding")
    A("")
    A("| criterion | weight | overlap | text |")
    A("|---|---|---|---|")
    for d in weak:
        txt = d["criterion"].replace("|", "\\|")
        A(f"| {d['number']} | {d['weight']} | {d['overlap_with_instruction']:.2f} | {txt} |")
    A("")

    A("### W4. Weight distribution")
    A("")
    A(f"- rubric weights: {led['rubric_weight_histogram']}")
    A(f"- test weights: {led['test_weight_histogram']}")
    A(f"- rubric importance: {led['importance_histogram']}")
    A("")

    return "\n".join(out)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def run(root: Path) -> tuple:
    t = load_task(root)
    if t.fatal and (not t.rubric or not t.test_src or not t.instruction):
        return t, [
            Finding(
                "TASK_UNUSABLE",
                "BLOCKER",
                "0. Schema & contract",
                str(root),
                "; ".join(t.fatal),
                "The task directory does not satisfy the minimum file contract.",
                "Nothing downstream can be validated or scored.",
                "Restore the missing or unparseable artifacts, then re-run QC.",
            )
        ], {"weight_ledger": {"rubric_criteria": 0, "tests_defined": 0, "tests_available": 0,
                              "rubric_available": 0, "tests_penalty": 0, "rubric_penalty": 0,
                              "rubric_share": None, "importance_histogram": {},
                              "rubric_weight_histogram": {}, "test_weight_histogram": {}},
            "uncovered_requirements": [], "constraint_ledger": [],
            "weakest_instruction_grounding": [], "requirement_coverage": []}

    findings: list[Finding] = []
    for msg in t.fatal:
        findings.append(
            Finding(
                "TASK_FILE_CONTRACT",
                "ERROR",
                "0. Schema & contract",
                str(root),
                msg,
                "The task file contract is fixed so the harness can find every artifact.",
                "Harbor or the verifier may fail to locate the artifact at run time.",
                "Restore the missing file.",
            )
        )
    findings += check_schema(t)
    findings += check_rubric_weights(t)
    findings += check_critical_coverage(t)
    findings += check_tests(t)
    findings += check_overlap(t)
    findings += check_lane_balance(t)
    findings += check_environment(t)
    findings += check_truth(t)

    deduped: list[Finding] = []
    seen: set = set()
    for item in findings:
        key = (item.code, item.locus, item.problem)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return t, deduped, build_worksheets(t)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description="Authoring-time QC for a Kanao task directory.")
    ap.add_argument("task_dir", type=Path)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--quiet", action="store_true", help="findings only, no worksheets")
    args = ap.parse_args(argv)

    root = args.task_dir.resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 3

    t, findings, ws = run(root)
    verdict, code = verdict_of(findings)
    if any(f.code == "TASK_UNUSABLE" for f in findings):
        code = 3

    if args.json:
        print(json.dumps(
            {
                "task": root.name,
                "task_dir": str(root),
                "verdict": verdict,
                "exit_code": code,
                "counts": dict(Counter(f.severity for f in findings)),
                "findings": [f.as_dict() for f in findings],
                "worksheets": ws,
            },
            indent=2,
        ))
    else:
        print(render_markdown(t, findings, ws, args.quiet))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
