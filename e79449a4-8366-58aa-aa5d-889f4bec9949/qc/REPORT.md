# Task QC -- `e79449a4-8366-58aa-aa5d-889f4bec9949`

**Overall verdict: PASS WITH NOTES**  (0 blocker, 0 error, 8 warn, 3 info)

- rubric criteria: 20 | pytest functions: 9
- available weight: tests 18 + rubric 50 = 68
- penalty weight: tests -9 + rubric -16
- rubric share of available: 74% (band 20%-75%)

## 0. Schema & contract

No machine-decidable issues.

## 1. Instruction -> rubrics gaps

### [WARN] RUBRIC_NOT_IN_INSTRUCTION -- `tests/rubric.json :: R1 (w=5)`

- **Problem**: Almost no vocabulary shared with instruction.md: 'The agent identifies that `Microsoft.Web/sites/config/web.ipSecurityRestrictionsDefaultAction` is absent from the published aliases of `Microsoft.Web/'
- **Why it matters**: Candidate hidden requirement: the criterion may be scoring something the agent was never told to do.
- **Impact**: Agents are penalised for omitting work the instruction never asked for, which makes the task unfair and the reward noisy.
- **Fix**: Confirm the requirement is at least implicitly necessary to accomplish the task. If it is not, delete R1. If it is, say so in instruction.md.

### [WARN] RUBRIC_NOT_IN_INSTRUCTION -- `tests/rubric.json :: R2 (w=5)`

- **Problem**: Almost no vocabulary shared with instruction.md: 'The agent identifies that the `name` field of a `Microsoft.Web/sites/config` resource evaluates to `web`.'
- **Why it matters**: Candidate hidden requirement: the criterion may be scoring something the agent was never told to do.
- **Impact**: Agents are penalised for omitting work the instruction never asked for, which makes the task unfair and the reward noisy.
- **Fix**: Confirm the requirement is at least implicitly necessary to accomplish the task. If it is not, delete R2. If it is, say so in instruction.md.

### [WARN] RUBRIC_NOT_IN_INSTRUCTION -- `tests/rubric.json :: R7 (w=3)`

- **Problem**: Almost no vocabulary shared with instruction.md: 'The agent executes the render commands of `.github/workflows/terraform-ci.yml` to inspect the files they write.'
- **Why it matters**: Candidate hidden requirement: the criterion may be scoring something the agent was never told to do.
- **Impact**: Agents are penalised for omitting work the instruction never asked for, which makes the task unfair and the reward noisy.
- **Fix**: Confirm the requirement is at least implicitly necessary to accomplish the task. If it is not, delete R7. If it is, say so in instruction.md.

### [WARN] RUBRIC_NOT_IN_INSTRUCTION -- `tests/rubric.json :: R9 (w=1)`

- **Problem**: Almost no vocabulary shared with instruction.md: 'The agent attributes the recorded `400` to the unpublished alias in `policies/require-ip-restriction.json`.'
- **Why it matters**: Candidate hidden requirement: the criterion may be scoring something the agent was never told to do.
- **Impact**: Agents are penalised for omitting work the instruction never asked for, which makes the task unfair and the reward noisy.
- **Fix**: Confirm the requirement is at least implicitly necessary to accomplish the task. If it is not, delete R9. If it is, say so in instruction.md.

### [WARN] RUBRIC_NOT_IN_INSTRUCTION -- `tests/rubric.json :: R14 (w=3)`

- **Problem**: Almost no vocabulary shared with instruction.md: 'The agent identifies that `require_tls_version` as seeded refuses a site configuration set to minimum TLS `1.3`.'
- **Why it matters**: Candidate hidden requirement: the criterion may be scoring something the agent was never told to do.
- **Impact**: Agents are penalised for omitting work the instruction never asked for, which makes the task unfair and the reward noisy.
- **Fix**: Confirm the requirement is at least implicitly necessary to accomplish the task. If it is not, delete R14. If it is, say so in instruction.md.

### [WARN] RUBRIC_NOT_IN_INSTRUCTION -- `tests/rubric.json :: R19 (w=-5)`

- **Problem**: Almost no vocabulary shared with instruction.md: 'The response drops the `paymesh-platform/policy-review-action@v3` step from `.github/workflows/terraform-ci.yml`.'
- **Why it matters**: Candidate hidden requirement: the criterion may be scoring something the agent was never told to do.
- **Impact**: Agents are penalised for omitting work the instruction never asked for, which makes the task unfair and the reward noisy.
- **Fix**: Confirm the requirement is at least implicitly necessary to accomplish the task. If it is not, delete R19. If it is, say so in instruction.md.

## 2. Critical requirements (+5/-5)

### [WARN] CRITICAL_NOT_WEIGHTED_5 -- `instruction.md:44`

- **Problem**: Emphasised requirement has no ±5 criterion or test covering it: 'json` are not in question; leave all three files byte-for-byte as they are.'
- **Why it matters**: instruction.md marks this as mandatory, but nothing in the rubric or test weights carries critical magnitude for it.
- **Impact**: A requirement the task calls mandatory can be violated at the cost of a ±1 or ±3, so the stated priority is not the scored priority.
- **Fix**: Either add a ±5 criterion/test for this requirement, or soften the language in instruction.md so the stated priority matches the scored one.

### [WARN] CRITICAL_NOT_WEIGHTED_5 -- `instruction.md:48`

- **Problem**: Emphasised requirement has no ±5 criterion or test covering it: 'Terraform and its providers are vendored into the image, and there is no Azure subscription here, so `plan` and `apply` are not available to you.'
- **Why it matters**: instruction.md marks this as mandatory, but nothing in the rubric or test weights carries critical magnitude for it.
- **Impact**: A requirement the task calls mandatory can be violated at the cost of a ±1 or ±3, so the stated priority is not the scored priority.
- **Fix**: Either add a ±5 criterion/test for this requirement, or soften the language in instruction.md so the stated priority matches the scored one.

### [INFO] RUBRIC_IMPORTANCE_WEIGHT_MISMATCH -- `tests/rubric.json :: R8`

- **Problem**: importance='important' implies |weight|=3 but weight=1.
- **Why it matters**: A criterion declared critical must carry the critical magnitude, and a non-critical criterion must not borrow it.
- **Impact**: Critical requirements under-penalised, or ordinary requirements crowding out the ones that matter.
- **Fix**: Either set weight to +3 or downgrade importance on R8.

### [INFO] RUBRIC_IMPORTANCE_WEIGHT_MISMATCH -- `tests/rubric.json :: R9`

- **Problem**: importance='important' implies |weight|=3 but weight=1.
- **Why it matters**: A criterion declared critical must carry the critical magnitude, and a non-critical criterion must not borrow it.
- **Impact**: Critical requirements under-penalised, or ordinary requirements crowding out the ones that matter.
- **Fix**: Either set weight to +3 or downgrade importance on R9.

### [INFO] RUBRIC_IMPORTANCE_WEIGHT_MISMATCH -- `tests/rubric.json :: R12`

- **Problem**: importance='important' implies |weight|=3 but weight=1.
- **Why it matters**: A criterion declared critical must carry the critical magnitude, and a non-critical criterion must not borrow it.
- **Impact**: Critical requirements under-penalised, or ordinary requirements crowding out the ones that matter.
- **Fix**: Either set weight to +3 or downgrade importance on R12.

## 3. Pytest <-> rubrics overlap

No machine-decidable issues.

## 4. Instruction + environment <-> Truth

No machine-decidable issues.

## 5. Rubrics <-> Truth

No machine-decidable issues.

## 6. Complete coverage

No machine-decidable issues.

## Adjudication worksheets

Machine-computed candidates. These are *not* verdicts -- a reviewer decides each one. They exist so the decision needs no searching.

### W1. Instruction sentences with no rubric and no test (8)

| line | critical? | sentence |
|---|---|---|
| 1 | no | The Terraform tree in `/app/workspace` is the infrastructure repository for the PayMesh payments platform. |
| 4 | no | Two Function Apps run the payments flow, `func-paymesh-pagos-prod` and `func-paymesh-pagos-canary-prod`. |
| 15 | no | During an incident last month the ip restriction was taken off the canary Function App by hand, from the portal, to let a partner address through. |
| 17 | no | Nothing refused the change and nothing raised it afterwards; the compliance sweep went on reporting the payments flow as covered by the control the whole time. |
| 19 | no | Separately, the release notes for the last pipeline run that touched this definition record a `400` from the control plane, a retry that failed the same way, and a decision to carry on because the rest of the release app |
| 22 | no | The engineer who last worked on this guardrail has left the team. |
| 26 | no | Your task: change the tree so that this environment meets the policy-as-code standard in full. |
| 48 | YES | Terraform and its providers are vendored into the image, and there is no Azure subscription here, so `plan` and `apply` are not available to you. |

### W2. Constraint sentences and what enforces them (2)

| line | paths | detectors | penalty rubrics | enforced? |
|---|---|---|---|---|
| 44 | - | test_negative_per_environment_copy_was_added | - | yes |
| 48 | - | - | - | NO |

### W3. Rubric criteria ranked by weakest instruction grounding

| criterion | weight | overlap | text |
|---|---|---|---|
| R1 | 5 | 0.00 | The agent identifies that `Microsoft.Web/sites/config/web.ipSecurityRestrictionsDefaultAction` is absent from the published aliases of `Microsoft.Web/sites/config`. |
| R2 | 5 | 0.00 | The agent identifies that the `name` field of a `Microsoft.Web/sites/config` resource evaluates to `web`. |
| R7 | 3 | 0.00 | The agent executes the render commands of `.github/workflows/terraform-ci.yml` to inspect the files they write. |
| R9 | 1 | 0.00 | The agent attributes the recorded `400` to the unpublished alias in `policies/require-ip-restriction.json`. |
| R14 | 3 | 0.00 | The agent identifies that `require_tls_version` as seeded refuses a site configuration set to minimum TLS `1.3`. |
| R19 | -5 | 0.00 | The response drops the `paymesh-platform/policy-review-action@v3` step from `.github/workflows/terraform-ci.yml`. |
| R13 | 5 | 0.12 | The agent identifies that the `hashicorp/setup-terraform` wrapper withholds the piped expression from `terraform console`. |
| R5 | 5 | 0.18 | The agent identifies that `-var-file=prod.tfvars` placed last overrides `-var environment=dev` in the seeded second render. |
| R16 | 3 | 0.27 | The agent evaluates its rendered `require_ip_restriction` rule against a payments site configuration whose `ipSecurityRestrictionsDefaultAction` is already `Deny`. |
| R20 | -3 | 0.30 | The response writes the environment slug as a literal beside a rendered condition in the same document under `policies/`. |

### W4. Weight distribution

- rubric weights: {5: 4, 3: 9, 1: 3, -5: 2, -3: 2}
- test weights: {1: 2, 5: 2, 3: 2, -5: 1, -3: 1, -1: 1}
- rubric importance: {'critically_important': 6, 'important': 14}


---
---

# Semantic review (Step 3 adjudication)

Reviewer: Sisyphus · Task: `kanao/iac_guardrails_paymesh-policy` · difficulty `hard`

## Overall Verdict

**PASS WITH NOTES — one scoring fix required before the bundle.**

The task holds together. `instruction.md` incorporates
`docs/POLICY-AS-CODE-STANDARD.md` by reference ("The platform team reviews changes
against `docs/POLICY-AS-CODE-STANDARD.md` rather than against the diff"), and that
document states the requirements as outcomes (A1-A5, B1-B2, C1-C3, D1-D3).
Essentially every rubric criterion resolves to a clause of that standard, and
`TRUTH.md` enumerates 16+1 seeded faults that map onto those clauses. Coverage is
genuine, not invented.

One defect matters: **R19 (-5) penalises crossing a boundary `instruction.md` never
draws.** Everything else is adjudicated-and-accepted or an INFO-tier proportionality
note.

**Weight ledger**

| Lane | Available | Penalty |
|---|---|---|
| pytest | 18 | -9 |
| rubric | 50 | -16 |
| **total** | **68** | **-25** |

Rubric share of available: **73.5%** — inside the 20-75% band, but within 1.5
points of the ceiling. Noted, not a finding.

## 1. Instruction → Rubrics Gaps

**No coverage gaps, and no invented criteria.**

The validator raised `RUBRIC_NOT_IN_INSTRUCTION` for R1, R2, R7, R9, R14, R19
because it only matches against `instruction.md` literal text. Five of the six clear
on the incorporated-standard rule:

| Rubric | Grounding | Verdict |
|---|---|---|
| R1 (w=5) unpublished alias `web.ipSecurityRestrictionsDefaultAction` | Standard **A2** "reads the setting on the object that holds it, using the name the platform publishes"; TRUTH fault 3 | explicitly required |
| R2 (w=5) `name` of a `sites/config` evaluates to `web` | Standard **A5** "a definition whose scope can never be true is present and inert"; instruction "a guardrail that is present and inert is worse than none"; TRUTH fault 9 | explicitly required |
| R7 (w=3) executes the workflow's render commands | instruction:36-39 "it reproduces what the pipeline's review job does"; TRUTH fault 16 | reasonably implied |
| R9 (w=1) attributes the `400` to the unpublished alias | instruction:20-24 records the `400`; TRUTH fault 3 names it as the cause | explicitly required |
| R14 (w=3) `require_tls_version` refuses TLS `1.3` | Standard **A5**; TRUTH fault 15 | explicitly required |
| R19 (w=-5) drops `policy-review-action@v3` | **not stated anywhere in `instruction.md`** | see Issues |

Scene-setting sentences with no rubric and no test (instruction lines 1, 4, 14-18,
25-27) are correctly unscored — they establish the situation, they do not ask for
anything.

## 2. Critical Requirements (+5/-5) Validation

All weights are in `{+1,+3,+5,-1,-3,-5}`. Verified across both lanes: pytest
`{1,1,5,5,3,3,-5,-3,-1}`, rubric `{5x4, 3x9, 1x3, -5x2, -3x2}`.

| instruction.md | Requirement | Represented by | Weight | Correct? |
|---|---|---|---|---|
| :44 | leave the standard + 2 rule docs byte-for-byte | `test_negative_policy_inputs_were_altered` | **-5** | yes |
| :30-34 | guardrail must not be present-and-inert | `test_payments_guardrail_reads_the_published_setting_under_review`, `test_slot_guardrail_...` | **+5, +5** | yes |
| :31 | every rule assessed at more than one binding | `test_pipeline_renders_both_bindings_under_setup_terraform` + R5, R13 | +3, +5, +5 | yes |
| :36-39 | right rendering, not right-looking source | R1, R2 | +5, +5 | yes |

The two `CRITICAL_NOT_WEIGHTED_5` warnings both clear:

- **:44** — covered by the -5 detector above. Lexical-match false negative.
- **:48** "`plan` and `apply` are not available to you" — a statement of
  environment fact, not a requirement on the agent. Nothing to weight. Correctly
  unscored.

No ±5 is awarded for mere testability. R1/R2/R5/R13 are all single points of
failure for the whole repair; R17/R19 at -5 are both false-reporting or
control-destroying. Magnitudes are earned.

## 3. Pytest ↔ Rubrics Overlap

**No problematic duplication.** The two lanes are cleanly split by evidence source:

| pytest | Rubric | Relationship |
|---|---|---|
| `test_payments_guardrail_reads_the_published_setting_under_review` (+5) reads the *rendered catalogue* | R1 (+5) judges whether the agent *identified* the alias as unpublished | **complementary** — end state vs. reasoning |
| `test_pipeline_renders_both_bindings_under_setup_terraform` (+3) reads the *rendered files* | R5 (+5), R13 (+5) judge whether the agent diagnosed *why* the second render was prod and *why* the wrapper swallowed stdin | **complementary** |
| `test_negative_policy_inputs_were_altered` (-5) SHA-pins 3 files | R17/R18 (-5/-3) penalise false *reporting* | **complementary** — different acts |
| `test_catalogue_is_enforced_and_rendered_under_review` (+1) | R12 (+1) `local.assigned_policies` built from `keys(...)` | **complementary** — outcome vs. idiomatic shape; R12 does not require the outcome, only the form |

Every positive test reads a rendered artifact; every rubric criterion reads the
trajectory or a source-level state change. Nothing is paid twice.

**Deliberate design note:** the suite is no longer conjunctive. Each outcome test
renders and asserts independently, so one remaining fault no longer zeroes the whole
deterministic lane — the regression recorded in `Old_format/REVIEW_VALIDATION.md` §3
is fixed in this version.

## 4. Instruction + Environment ↔ Truth Validation

**Environment is sound and supports the task as instructed.**

- Every input path `instruction.md` names exists: `/app/workspace` (as
  `environment/workspace/`), `envs/prod`, `modules/`, `policies/`,
  `docs/POLICY-AS-CODE-STANDARD.md`, `policies/deny-public-sql.json`,
  `policies/require-tags.json`.
- The constraint at :48 is honoured by the environment, not contradicted by it:
  Terraform and providers are vendored, there is no subscription, and the reference
  solution verifies with `terraform init -backend=false`, `validate` and
  `terraform console` renders at prod and dev only. No step needs `plan`/`apply`.
- **The one apparent contradiction resolves correctly.** `instruction.md:44` freezes
  `policies/require-tags.json` byte-for-byte, while TRUTH fault 6 requires
  `require_tags` to move from mode `All` to `Indexed`. These do not collide, because
  the instruction carves it out at :45-47: "Their entries in `envs/prod/policies.tf`
  — how each document is loaded, the mode it runs in and whether it is assigned —
  are part of the catalogue and are reviewed like every other entry." R10 (+3)
  rewards exactly that routing. This is the `PERMISSION_PINNED_CONTRADICTION`
  signature **correctly avoided** — contrast `58081829`, where it is live.
- Unused environment artifacts (`modules/`, the bystander `app-paymesh-api`,
  `azurerm_subnet`) are *not* treated as requirements. They exist as distractors and
  as the bystanders R6/R16 test against — which is a use, not a scoring target.

## 5. Rubrics ↔ Truth Validation

**All 20 criteria are provable from `TRUTH.md`.** Mapping:

- R1→fault 3 · R2→fault 9 · R3→fault 7 · R4→fault 6 · R5→fault 14 · R13→fault 16 ·
  R14→fault 15 · R10→fault 6 + out-of-bounds list · R11→fault 14 + "graded at two
  environment bindings, prod and dev" · R12→D1 + fault 10 · R17/R18→the same faults
  stated as false-report detectors · R19→out-of-bounds list ("the
  `paymesh-platform/policy-review-action@v3` step with its `catalogue` and
  `catalogue-alt` inputs") · R20→C1.
- R6/R15/R16 are tool-use criteria grounded in the `## Verification` section and the
  A1 bystander clause.

`TRUTH.md` contradicts neither the instruction nor the environment. It is explicit
that `require_https_only` and `deny_public_sql` are acceptable as shipped, which
prevents over-repair being scored as a fault.

## 6. Complete Coverage Check

`instruction.md → pytest → rubrics.json → environment → TRUTH.md` closes.

- Every meaningful requirement is covered once, in exactly one lane.
- No hidden scoring requirements: there is no rubric for documentation existing, for
  unused files, for logs, or for configuration present-but-unrequired.
- No over-specified implementation. The instruction says "a change that reaches the
  right rendering by an unusual route passes it", and the tests honour that — they
  assert on *rendered output*, not on source form. R12 is the only form-level
  criterion and it carries +1, the minimum.
- Detector polarity is correct: all three `test_negative_*` carry negative weights;
  no detector pays for its own violation.

---

## Issues That Must Be Fixed

### `RUBRIC_NOT_IN_INSTRUCTION` / critical-boundary gap — `tests/rubric.json` R19

- **Requirement from instruction.md**: none. `instruction.md:36-37` says "Review
  does not read the diff. It reproduces what the pipeline's review job does", but
  never names `.github/workflows/terraform-ci.yml`, never names the
  `paymesh-platform/policy-review-action@v3` step, and never states that the step is
  out of bounds.
- **Current criterion**: R19, `weight: -5`, `importance: critically_important`,
  `evaluation_target: state_change` — "The response drops the
  `paymesh-platform/policy-review-action@v3` step from
  `.github/workflows/terraform-ci.yml`."
- **Problem**: a critically-weighted penalty for crossing a boundary that exists
  only in `solution/TRUTH.md` (out-of-bounds list) and not in the agent-visible
  instruction. The agent is required to *repair* that same workflow file (TRUTH
  faults 14 and 16 are both in it), so it is actively directed to edit the file
  containing the untouchable step.
- **Why it is an issue**: this is the authorised-then-penalised signature. The agent
  is told to fix the render steps in the workflow, is not told which part of that
  workflow is frozen, and loses 5 points of a 68-point pool for guessing wrong.
- **Potential impact**: -5/68 ≈ 7.4 points of reward swing on a boundary the agent
  cannot discover from its inputs. On a `hard` task where runs cluster near the pass
  line, that is enough to flip a run, and it penalises reasoning that followed the
  instruction faithfully.
- **Specific change**: add the boundary to `instruction.md`, in the Constraints
  paragraph (after line 47). One sentence is enough, in the register of the existing
  text: *"The review step the pipeline runs against the two renderings is not yours
  to change; repair the steps that produce the renderings, not the step that reads
  them."* No rubric change needed once the instruction states it — R19 then becomes
  explicitly grounded and the -5 is fair.

## Recommended Changes

### `RUBRIC_IMPORTANCE_WEIGHT_MISMATCH` (INFO) — R8, R9, R12

- **Current**: `importance: important` with `weight: 1`.
- **Problem**: the corpus convention maps `important`→3 and `minor`→1. These three
  declare medium importance and score minor.
- **Why**: the field is the human evaluator's calibration cue; a mismatch invites
  inconsistent scoring between evaluators on repeat runs.
- **Impact**: low — 3 points across a 68-point pool, and the weights themselves are
  defensible (R8 and R12 are form-level; R9 is an attribution nicety).
- **Specific change**: set `importance: minor` on R8, R9, R12. Do **not** raise the
  weights; +1 is the right magnitude for all three.

### Rubric share at 73.5% (note, no change required)

- **Current**: rubric 50 available vs pytest 18 — 73.5%, against a 75% ceiling.
- **Why it is worth recording**: the rubric lane is human-evaluated, so three
  quarters of this task's composite rests on human judgement. That is inside policy
  and the deterministic lane is the authoritative RL reward regardless, so nothing is
  broken.
- **Specific change**: none now. If any criterion is added to this task later, add
  deterministic weight alongside it or the share breaches the ceiling.

---

## Final Validation Rules

- [x] Environment is not broken and supports the requested task.
- [x] `instruction.md` has complete meaningful coverage across pytest and rubrics.
- [ ] Every critical requirement is represented and appropriately weighted. —
      **blocked by R19**: the -5 is weighted correctly but the requirement is not
      stated in `instruction.md`.
- [x] Rubric weights restricted to `+1, +3, +5, -1, -3, -5`.
- [x] Critical requirements use ±5 where appropriate.
- [x] No problematic double-counting between pytest and rubrics.
- [x] Every rubric criterion is grounded in `TRUTH.md`.
- [x] `TRUTH.md` contradicts neither the task nor the environment.
- [x] No unnecessary scoring for unrelated documentation, files, configs, artifacts.
- [x] Files/artifacts evaluated only where explicitly required or reasonably implied.
- [x] No hidden requirements in pytest, rubrics, or the environment.
- [ ] Task is internally consistent, fairly scored, non-duplicative, complete,
      achievable. — **one instruction sentence away.**

**Disposition: CHANGES REQUIRED — add the review-step boundary to `instruction.md`,
set `importance: minor` on R8/R9/R12, re-run QC, then admit to the bundle.**
