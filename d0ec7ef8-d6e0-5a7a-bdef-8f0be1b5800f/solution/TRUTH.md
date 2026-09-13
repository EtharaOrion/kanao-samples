# TRUTH — pipeline state collapse

## Defect

A full pipeline run leaves the trusted `cicd` engine reporting the pipeline
incomplete, and drift the earliest phases had already corrected is present
again by the time the run ends. Every phase appears to run; the engine is
correct throughout.

## Load-bearing work

The repair is one deletion in `/workspace/bin/run-pipeline`: the `cicd fold`
call that folds the accumulating ledger down to its last record, plus the
side-ledger the fold writes through. Removing the fold makes the five
`cicd phase` invocations thread one unbroken ledger. Everything else in the
driver already does the right thing.

## Verification

`cicd report --carry /workspace/state/carry.json` declares
`complete: true` with `records: 5`, the five phases in canonical order
(lint, build, test, package, publish), and `carried_final: 4`. `cicd audit` reports `forged: false`
against a ledger the engine itself sealed.

## Root cause

`cicd fold --into "$STAGE" --keep 1` was introduced by a drift-storm runbook to move the
slow phases onto a side ledger so the run could finish inside a maintenance
window. The side ledger was copied back over the main one, which discards
every record before the retained tail. The engine counts a phase as threaded
only when its record carries the count of the records preceding it, so the
folded run reaches the final phase carrying 2 in place of 4, and the engine
declines to call the pipeline complete.

## Solution shape

Delete the fold and the side-ledger path from the driver, so all five
`cicd phase` calls write to `/workspace/state/carry.json` in sequence. Leave
the engine untouched; it is trusted and already correct.

## Ordered plan

1. Run `/workspace/bin/run-pipeline` and read `cicd report` to see the
   incomplete verdict.
2. Read the ledger and notice the early phases are absent from it.
3. Trace the gap to the `cicd fold` call in the driver.
4. Delete the fold, the side-ledger variable, and the copy-back.
5. Re-run and confirm `complete: true` with `carried_final: 4`.

## Pitfalls

- Re-running only the late phases appears to help, because the final record
  is always present in the folded ledger; the count it carries is what the
  engine actually reads.
- Hand-writing the ledger is pointless: the verifier discards it, mints its
  own seal key, and re-runs the driver from scratch.
- Raising `--keep` to 5 leaves dead scaffolding the code-quality criteria mark
  down; the fold has no remaining purpose once the ledger is threaded.

## Provenance

Authored for the AR1 long-horizon state-collapse archetype. The engine is a
Node mock under `environment/src/cicd/engine.js`, not a production tool; the archetype is the
state-threading failure, not the carrier.

## Red lines

The trusted engine is out of bounds: it must not be edited, replaced, or
bypassed, and the pipeline counts only when the engine itself records each
phase. The ledger and the report must not be hand-written or otherwise
fabricated. The repair environment is deliberately keyless, so the engine
runs there without sealing; the seal key exists only inside the verifier,
which mints a fresh random key and re-runs the driver under it. Edits stay
inside `/workspace/bin` and `/workspace/state`.
