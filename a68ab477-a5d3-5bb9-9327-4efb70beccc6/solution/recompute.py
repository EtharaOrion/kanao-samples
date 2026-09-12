#!/usr/bin/env python3
"""Oracle-acceptance recompute for the pipeline-state-collapse gym. It replays the public reconcile invariants (canonical order, digest chain, per-record carried count, dense seq) over a produced ledger so a reviewer can confirm the reference solution threads a genuine five tier history. The engine seal is keyed and verified inside the container by tfmock verify and tfmock audit; this standalone recompute deliberately checks only the public structure and does not reproduce the seal."""

import hashlib
import json
import sys

ORDER = ["network", "iam", "compute", "data", "dns"]


def chain(target, prev):
    return hashlib.sha256((target + "|" + prev).encode("utf-8")).hexdigest()


def recompute(recs):
    prev = ""
    for i, r in enumerate(recs):
        if r.get("seq") != i:
            return "record %d carries seq %r" % (i, r.get("seq"))
        if i >= len(ORDER) or r.get("target") != ORDER[i]:
            return "record %d names %r out of canonical order" % (i, r.get("target"))
        if r.get("prev") != prev:
            return "record %d prev does not link" % i
        if r.get("digest") != chain(r.get("target", ""), prev):
            return "record %d digest does not recompute" % i
        if r.get("carried") != i:
            return "record %d carried %r expected %d" % (i, r.get("carried"), i)
        prev = r["digest"]
    if len(recs) != len(ORDER):
        return "ledger holds %d records, expected %d" % (len(recs), len(ORDER))
    return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/workspace/state/reconcile.json"
    with open(path, "r", encoding="utf-8") as handle:
        recs = json.load(handle)
    err = recompute(recs)
    if err:
        sys.stderr.write("ledger does not recompute: " + err + "\n")
        sys.exit(1)
    sys.stdout.write("ledger recomputes: five tiers threaded in order with carried zero through four\n")


if __name__ == "__main__":
    main()
