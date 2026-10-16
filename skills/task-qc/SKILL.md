---
name: task-qc
description: >-
  Authoring-time QC for a Kanao task directory. Validates instruction.md against
  tests/rubric.json, tests/test_outputs.py + test_weights.json, environment/ and
  solution/TRUTH.md for internal consistency, fair scoring, non-duplication,
  grounding, and absence of hidden requirements. Produces a structured validation
  report plus a machine-readable findings file that steers the next forge pass.
  Use before a task leaves the author's hands and before any bundle goes to
  Crucible. Triggers: "QC this task", "validate my task", "review rubric vs
  instruction", "check scoring consistency", "is this task shippable",
  "pre-crucible check".
budget_minutes: 20
---

# Task QC

Fast, repeatable consistency review of one task directory. Target: **20 minutes
wall-clock per task.** It is a pre-Crucible gate, not a replacement for Crucible.

## What this is for

Crucible is comprehensive and expensive. Most task defects are not subtle: a
rubric criterion nothing in the instruction asks for, a detector that pays for the
violation it was written to punish, an instruction that authorises an edit the
grader hash-pins. Those are cheap to catch and expensive to discover after eight
rollouts have been burned.

This skill catches that class in minutes so Crucible only ever sees tasks that
already hold together.

## Inputs

A task directory with the standard layout:

```
<uuid>/
├─ task.toml
├─ instruction.md
├─ environment/
├─ solution/{TRUTH.md,solve.sh}
└─ tests/{test_outputs.py,test_weights.json,rubric.json,test.sh,score_reward.py}
```

## Procedure

### Step 1 — deterministic pass (30 seconds)

```bash
python3 skills/task-qc/validate_task.py <task_dir>              # report
python3 skills/task-qc/validate_task.py <task_dir> --json       # for forge
```

Exit codes: `0` clean or notes only, `1` ERROR findings, `2` BLOCKER, `3` task
directory unusable.

This settles everything mechanically decidable — weight sets, polarity, orphan
weights, lane balance, path grounding, instruction↔pin contradictions — and prints
four worksheets of pre-computed candidates for the judgement calls. **Read the
worksheets before opening any file.** They tell you where to look so you are not
searching the tree by hand; that is the whole reason the review fits in 20 minutes.

### Step 2 — read the three artifacts that need a human (8 minutes)

In this order, and only this far:

1. `instruction.md` end to end. While reading, keep one question live: *what does
   this task actually ask for?* Write the answer down in one sentence.
2. `tests/rubric.json`, against worksheet **W3** (criteria ranked by weakest
   instruction grounding). Start at the top of that list — the weakest-grounded
   criteria are where invented requirements live.
3. `solution/TRUTH.md` headings only, plus the sections any flagged criterion
   depends on.

Do not read the whole environment tree. Do not read every test body. The
deterministic pass already covered what needs covering there.

### Step 3 — adjudicate the six questions (8 minutes)

Each question has a worksheet behind it. Decide, record the decision, move on.

**1. Instruction → rubric coverage.** From worksheet **W1** (instruction sentences
with no rubric and no test): is each one a real requirement, or scene-setting? Real
and uncovered → coverage gap. From **W3**: is each weakly-grounded criterion
required by the instruction *explicitly or as a reasonable implication*? Neither →
delete it.

The test for "reasonable implication" is: *could the task be accomplished correctly
without doing this?* If yes, it is not implied, and scoring it is a hidden
requirement. Presence in the environment is not implication. Documentation existing
is not implication. An unused config file is not implication.

One exception the deterministic pass cannot see: an instruction may **incorporate a
specification by reference** — "the platform team reviews changes against
`docs/STANDARD.md`" makes every outcome in that document a first-class requirement
even though none of its words appear in `instruction.md`. When the instruction
names a spec in the tree as the thing the work is judged against, read that spec and
treat it as part of the instruction. Criteria grounded there are explicitly
required, not invented, and most `RUBRIC_NOT_IN_INSTRUCTION` warnings on such a task
will clear this way. Record that you checked, and against which document.

**2. Critical requirements.** Every requirement the instruction marks mandatory,
critical, or must/must-not must map to a ±5 somewhere. The deterministic pass flags
unmatched ones as `CRITICAL_NOT_WEIGHTED_5`. That match is lexical, so clear it two
ways before acting: confirm the sentence is a genuine requirement on the agent and
not emphatic scene-setting or a statement of environment fact, and confirm no ±5
already covers it under different vocabulary — a detector named
`test_negative_policy_inputs_were_altered` covers "leave these files byte-for-byte"
while sharing almost no words with it. Conversely, no ±5 may exist for a
requirement that is merely easy to test. Reserve the magnitude for requirements
whose violation makes the submission wrong.

**3. Pytest ↔ rubric overlap.** For each `PYTEST_RUBRIC_DUPLICATION` finding,
decide which it is:

- *Complementary* — the test decides an objective end state, the rubric judges the
  behaviour or reasoning that produced it. Keep both.
- *Duplicative* — both decide the same condition from the same evidence. The
  requirement is paid twice. Keep the test, re-aim or drop the rubric criterion.

The deciding question is **what evidence does each one read?** Same artifact, same
condition → duplication.

**4. Instruction + environment → Truth.** Can the task be completed as written, in
the environment as shipped? Walk the reference solution once. Every input the
instruction names must exist; every output it demands must be produced and read
back by something. Unused files in the environment are not requirements — do not
turn them into any.

**5. Rubric → Truth.** Every criterion must be provable from `TRUTH.md`. If an
evaluator cannot point at a passage that settles it, it cannot be scored
consistently and does not belong in the rubric.

**6. Coverage without invention.** Close the loop:
`instruction.md → pytest → rubric.json → environment → TRUTH.md`. Every meaningful
requirement covered once; nothing scored that the instruction never asked for; no
rubric requiring one particular implementation when the instruction only demands an
observable outcome.

### Step 4 — write the report (4 minutes)

Use `REPORT_TEMPLATE.md`. Every issue carries all seven fields; an issue without a
specific recommended change is not a finding, it is a feeling.

Write to `<task_dir>/qc/REPORT.md` and keep the JSON next to it:

```bash
mkdir -p <task_dir>/qc
python3 skills/task-qc/validate_task.py <task_dir>        > <task_dir>/qc/REPORT.md
python3 skills/task-qc/validate_task.py <task_dir> --json > <task_dir>/qc/findings.json
```

Then append your Step 3 adjudications to `REPORT.md` under the matching sections.

## Verdict rules

| Verdict | Meaning | Action |
|---|---|---|
| `BLOCKED` | any BLOCKER: the task cannot be scored as written | back to forge, do not run rollouts |
| `CHANGES REQUIRED` | any ERROR: real inconsistency or unfairness | back to forge with `findings.json` |
| `PASS WITH NOTES` | WARN only, all adjudicated and accepted | eligible for the bundle |
| `PASS` | nothing outstanding | eligible for the bundle |

A task is shippable only when all of the following are confirmed in writing:

- Environment is not broken and supports the task as instructed.
- `instruction.md` has complete meaningful coverage across pytest and the rubric.
- Every critical requirement is represented and weighted ±5.
- All weights are in `{+1, +3, +5, -1, -3, -5}`.
- No problematic double-counting between pytest and the rubric.
- Every rubric criterion is grounded in `TRUTH.md`.
- `TRUTH.md` contradicts neither the instruction nor the environment.
- Nothing is scored for incidental documentation, unused files, or artifacts the
  task neither requires nor reasonably implies.
- No hidden requirements anywhere in pytest, the rubric, or the environment.

## Steering forge with the output

`findings.json` is the handoff. Each finding carries `code`, `severity`, `locus`,
`problem`, `why`, `impact`, `fix` — the `fix` field is written to be actionable
without the report around it.

Feed the whole file back as the forge revision brief. Address findings in severity
order: BLOCKER, then ERROR, then adjudicated WARNs. Re-run the deterministic pass
after each forge revision; it is 30 seconds, so there is no reason to batch.

Re-run until the exit code is 0 **and** the Step 3 adjudications are recorded. Then
the task joins the bundle.

## Bundle gate

Crucible runs on a bundle of **5 tasks that have each cleared this QC**. Do not
send a bundle containing a task at `CHANGES REQUIRED` or `BLOCKED` — Crucible will
spend its budget rediscovering what cost 30 seconds here.

## Scope — what this skill does not do

- It does not run the task, the agent, or the verifier. Oracle and nop proof runs
  are a separate gate.
- It does not check trajectories. That is `TRAJECTORY_QC_CHECKLIST.md`, which runs
  after rollouts.
- It does not calibrate difficulty. That needs pass@8 data, which needs rollouts.
- It does not judge whether the task is interesting or hard enough. That is
  Crucible's job, and it is why Crucible still runs.

## Known defect signatures

Patterns already confirmed in this corpus. The validator detects each one; these
are the names to use when reporting them.

| Signature | What it looks like | Validator code |
|---|---|---|
| Authorised-then-pinned | instruction grants latitude over a path the grader hash-pins, so following the instruction loses reward | `PERMISSION_PINNED_CONTRADICTION` |
| Conjunctive suite | every positive test routes through one shared helper, so one remaining fault zeroes the whole deterministic lane and the rubric ends up owning 100% of reward | `TESTS_SHARE_SINGLE_FIXTURE` |
| Paid detector | a `test_negative_*` detector carries positive weight, paying for the violation it exists to punish | `DETECTOR_POSITIVE_WEIGHT` |
| Hygiene before correctness | form and evidence-hygiene credit awarded independently of diagnosis correctness, flooring every run at the same nonzero value | adjudicate at Step 3.2 |
| Invented requirement | rubric criterion with no basis in the instruction, usually describing something that merely exists in the environment | `RUBRIC_NOT_IN_INSTRUCTION` |
| Unreachable credit | a test asserts on a path nothing in the environment or the reference solution produces, so its weight can never be earned | `TEST_PATH_UNGROUNDED` |
| Lane imbalance | rubric share of available weight outside 20–75%, making reward either unreproducible or the behavioural requirements unscored | `RUBRIC_SHARE_OUT_OF_BAND` |
