# TRUTH — release state collapse

## Defect

A full release run leaves the trusted `relx` engine reporting the release
incomplete, and drift the earliest stages had already corrected is present
again by the time the run ends. Every stage appears to run; the engine is
correct throughout.

## Load-bearing work

The repair is one deletion in `/workspace/bin/run-release`: the `relx snapshot`
call that folds the accumulating ledger down to its last record, plus the
side-ledger the fold writes through. Removing the fold makes the five
`relx stage` invocations thread one unbroken ledger. Everything else in the
driver already does the right thing.

## Verification

`relx manifest --ledger /workspace/state/ledger.json` declares
`complete: true` with `records: 5`, the five stages in canonical order
(compile, migrate, canary, promote, finalize), and `carried_final: 4`. `relx audit` reports `forged: false`
against a ledger the engine itself sealed.

## Root cause

`relx snapshot --into "$SIDE" --last 1` was introduced by a drift-storm runbook to move the
slow stages onto a side ledger so the run could finish inside a maintenance
window. The side ledger was copied back over the main one, which discards
every record before the retained tail. The engine counts a stage as threaded
only when its record carries the count of the records preceding it, so the
folded run reaches the final stage carrying 2 in place of 4, and the engine
declines to call the release complete.

## Solution shape

Delete the fold and the side-ledger path from the driver, so all five
`relx stage` calls write to `/workspace/state/ledger.json` in sequence. Leave
the engine untouched; it is trusted and already correct.

## Ordered plan

1. Run `/workspace/bin/run-release` and read `relx manifest` to see the
   incomplete verdict.
2. Read the ledger and notice the early stages are absent from it.
3. Trace the gap to the `relx snapshot` call in the driver.
4. Delete the fold, the side-ledger variable, and the copy-back.
5. Re-run and confirm `complete: true` with `carried_final: 4`.

## Pitfalls

- Re-running only the late stages appears to help, because the final record
  is always present in the folded ledger; the count it carries is what the
  engine actually reads.
- Hand-writing the ledger is pointless: the verifier discards it, mints its
  own seal key, and re-runs the driver from scratch.
- Raising `--keep` to 5 leaves dead scaffolding the code-quality criteria mark
  down; the fold has no remaining purpose once the ledger is threaded.

## Provenance

Authored for the AR1 long-horizon state-collapse archetype. The engine is a
Go mock under `environment/src/relx/main.go`, not a production tool; the archetype is the
state-threading failure, not the carrier.

## Red lines

The trusted engine is out of bounds: it must not be edited, replaced, or
bypassed, and the release counts only when the engine itself records each
stage. The ledger and the report must not be hand-written or otherwise
fabricated. The repair environment is deliberately keyless, so the engine
runs there without sealing; the seal key exists only inside the verifier,
which mints a fresh random key and re-runs the driver under it. Edits stay
inside `/workspace/bin` and `/workspace/state`.
