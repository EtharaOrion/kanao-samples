canonical-release-order: compile migrate canary promote finalize
carried-invariant: each recorded stage carries a prior-history count equal to its own sequence index, so the record at index i must report carried i
history-rule: a release is complete only when every stage in the canonical order is present, the hash chain links end to end, and no stage was recorded against a truncated or isolated view of the accumulated ledger
authority: this declaration is the operating contract for the release engine and is not part of the repair surface
