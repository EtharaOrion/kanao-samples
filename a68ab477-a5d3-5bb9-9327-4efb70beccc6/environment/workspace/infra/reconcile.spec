canonical-reconcile-order: network iam compute data dns
carried-invariant: the record at index i must report a carried count of exactly i, so every applied tier names how many earlier tiers it inherited
history-rule: a reconcile is complete only when all five tiers are applied in order onto one accumulating ledger, the digest chain links end to end, and no tier is applied against a snapshotted or truncated ledger
seal-rule: every record carries an engine seal and a matching journal line, so a ledger the engine did not thread itself cannot pass an audit
drift-rule: an early correction dropped from the ledger lets its drift silently reappear, so the ledger must accumulate every tier across the whole apply sequence
authority: this operating contract is not part of the repair surface and must not be edited
