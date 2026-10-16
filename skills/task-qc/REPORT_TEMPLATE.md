# Task QC Report — `<uuid>`

- **Task name**: `<task.toml [task].name>`
- **Declared difficulty**: `<trivial|easy|medium|hard|expert>`
- **Reviewer**: `<name>`
- **Date**: `<YYYY-MM-DD>`
- **Validator exit code**: `<0|1|2|3>`

## Overall Verdict

`PASS` | `PASS WITH NOTES` | `CHANGES REQUIRED` | `BLOCKED`

One paragraph: what the task asks for, whether the five artifacts agree on it, and
the single most important thing to fix. If the verdict is anything other than
`PASS`, name the finding that drove it.

**Weight ledger**

| Lane | Available | Penalty |
|---|---|---|
| pytest | | |
| rubric | | |
| **total** | | |

Rubric share of available: `<pct>` (band 20–75%).

---

## 1. Instruction → Rubrics Gaps

Requirements in `instruction.md` absent from `rubrics.json`, and rubric criteria
with no explicit or implied basis in `instruction.md`.

State explicitly for each flagged criterion whether it is **explicitly required**,
**reasonably implied**, or **invented**. The test for reasonably implied: *could the
task be accomplished correctly without doing this?* If yes, it is invented.

## 2. Critical Requirements (+5/-5) Validation

| instruction.md locus | Requirement | Marked as | Represented by | Weight | Correct? |
|---|---|---|---|---|---|

Confirm: every weight in `{+1,+3,+5,-1,-3,-5}`; every critical requirement at ±5;
no ±5 awarded for mere testability.

## 3. Pytest ↔ Rubrics Overlap

| pytest test | Rubric | Shared condition | Classification | Action |
|---|---|---|---|---|

Classification is `complementary` or `duplicative`. Complementary means the test
decides an objective end state while the rubric judges the behaviour that produced
it. Duplicative means both read the same evidence to decide the same condition.

## 4. Instruction + Environment ↔ Truth Validation

Environment completeness, capability, and contradictions. Cover: every input path
the instruction names exists; every output it demands is produced and read back;
nothing in the environment contradicts `TRUTH.md`; the task is completable as
instructed. Note explicitly that unused environment artifacts were **not** treated
as requirements.

## 5. Rubrics ↔ Truth Validation

| Rubric | Grounded in TRUTH.md? | Passage | Action |
|---|---|---|---|

Every criterion needs a passage an evaluator can point at. No passage → not
scoreable → remove or document.

## 6. Complete Coverage Check

`instruction.md → pytest → rubrics.json → environment → TRUTH.md`

| Requirement (instruction.md locus) | pytest | Rubric | Grounded | Verdict |
|---|---|---|---|---|

Confirm: every meaningful requirement covered once; no hidden scoring requirements;
no invented pytest or rubric requirements; incidental environment artifacts
unscored; no over-specified implementation where the instruction demands only an
observable outcome.

---

## Issues That Must Be Fixed

Blockers and errors. One block each, all seven fields:

### `<CODE>` — `<exact file / criterion / test>`

- **Requirement from instruction.md**:
- **Current behaviour / criterion**:
- **Problem**:
- **Why it is an issue**:
- **Potential impact on correctness or scoring**:
- **Specific change recommended**:

## Recommended Changes

Adjudicated warnings worth acting on, same seven fields. Omit anything that is only
a stylistic preference.

---

## Final Validation Rules

Tick only what is confirmed. An unticked line blocks the bundle.

- [ ] Environment is not broken and supports the requested task.
- [ ] `instruction.md` has complete meaningful coverage across pytest and rubrics.
- [ ] Every critical requirement is represented and appropriately weighted.
- [ ] Rubric weights restricted to `+1, +3, +5, -1, -3, -5`.
- [ ] Critical requirements use ±5 where appropriate.
- [ ] No problematic double-counting between pytest and rubrics.
- [ ] Every rubric criterion is grounded in `TRUTH.md`.
- [ ] `TRUTH.md` contradicts neither the task nor the environment.
- [ ] No unnecessary scoring for unrelated documentation, files, configs, artifacts.
- [ ] Files/artifacts evaluated only where explicitly required or reasonably implied.
- [ ] No hidden requirements in pytest, rubrics, or the environment.
- [ ] Task is internally consistent, fairly scored, non-duplicative, complete, achievable.
