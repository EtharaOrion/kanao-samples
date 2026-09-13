# Reconcile the infrastructure stack

An infrastructure stack of five tiers (network, iam, compute, data, dns) is reconciled through a trusted engine named tfmock that records each applied tier onto an accumulating ledger at /workspace/state/reconcile.json and can print a status through tfmock report. The reconcile is carried out by the tooling under /workspace/bin.

Something is wrong with the reconcile. When the stack is reconciled end to end the engine reports that the reconcile is not complete even though every tier appears to run, and drift that the earliest tiers had already corrected is present again by the time the run finishes. The engine is trusted and correct; the fault is in how the reconcile is carried out across the run.

Find the cause and repair it so a full reconcile leaves the engine reporting a complete stack, with all five tiers threaded in order onto one ledger and no early correction lost. You may edit files under /workspace/bin and state under /workspace/state. You must not edit the trusted engine, and you must not hand write or otherwise fabricate the ledger or the report; a reconcile counts only when the engine itself records each tier.
