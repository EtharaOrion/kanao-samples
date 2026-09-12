#!/usr/bin/env python3
"""tfmock is the trusted, immutable mock-terraform reconciler for the pipeline-state-collapse gym. It threads an accumulating reconcile ledger across five ordered tiers, and every record it writes carries a keyed seal so a ledger that was not produced by this engine cannot pass an audit. The seal key lives outside the repair surface and the engine is the only writer of a record seal; a driver that hand writes a ledger can copy the public digest chain but cannot forge the seal. The engine lives outside the repair surface; the workspace driver that decides how to thread the ledger across the tiers is the only thing an operator edits."""

import hashlib
import hmac
import json
import os
import sys

ORDER = ["network", "iam", "compute", "data", "dns"]
PRED = {"iam": "network", "compute": "iam", "data": "compute", "dns": "data"}
KEY_PATH = "/usr/local/lib/tfmock/seal.key"


def die(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)


def load_key():
    if not os.path.exists(KEY_PATH):
        die("engine seal key is missing at " + KEY_PATH)
    with open(KEY_PATH, "rb") as handle:
        key = handle.read().strip()
    if not key:
        die("engine seal key is empty")
    return key


def chain(target, prev):
    return hashlib.sha256((target + "|" + prev).encode("utf-8")).hexdigest()


def seal(key, seq, target, prev, digest, carried):
    payload = "%d|%s|%s|%s|%d" % (seq, target, prev, digest, carried)
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        data = handle.read()
    if not data:
        return []
    try:
        return json.loads(data)
    except ValueError as err:
        die("ledger " + path + " is not readable: " + str(err))


def save(path, obj):
    blob = json.dumps(obj, indent=2) + "\n"
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        handle.write(blob)
    os.replace(path + ".tmp", path)


def journal(path, rec):
    with open(path + ".journal", "a", encoding="utf-8") as handle:
        handle.write("%d\t%s\t%s\t%s\n" % (rec["seq"], rec["target"], rec["digest"], rec["seal"]))


def seal_ok(key, rec):
    want = seal(key, rec.get("seq", -1), rec.get("target", ""), rec.get("prev", ""), rec.get("digest", ""), rec.get("carried", -1))
    return hmac.compare_digest(want, str(rec.get("seal", "")))


def verify(key, recs):
    prev = ""
    for i, r in enumerate(recs):
        if r.get("seq") != i:
            return "record %d carries seq %r" % (i, r.get("seq"))
        if r.get("target") not in ORDER:
            return "record %d names unknown tier %r" % (i, r.get("target"))
        if r.get("prev") != prev:
            return "record %d prev does not link" % i
        if r.get("digest") != chain(r.get("target", ""), prev):
            return "record %d digest does not recompute" % i
        if r.get("carried") != i:
            return "record %d carried %r expected %d" % (i, r.get("carried"), i)
        if not seal_ok(key, r):
            return "record %d seal does not verify" % i
        prev = r["digest"]
    return None


def parse(args):
    opts = {}
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            opts[a[2:]] = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        else:
            rest.append(a)
            i += 1
    return opts, rest


def cmd_apply(args):
    key = load_key()
    opts, rest = parse(args)
    target = rest[0] if rest else ""
    ledger = opts.get("state", "")
    if not target or not ledger:
        die("usage: tfmock apply <tier> --state <path>")
    if target not in ORDER:
        die("tier %s is not part of this stack" % target)
    recs = load(ledger)
    for r in recs:
        if r.get("target") == target:
            die("tier %s is already on this ledger" % target)
    last = recs[-1]["target"] if recs else ""
    if target in PRED:
        if last != PRED[target]:
            die("tier %s must follow %s, but the ledger last threaded %s" % (target, PRED[target], last))
    elif len(recs) != 0:
        die("tier %s must open a fresh reconcile, but the ledger already threads %d records" % (target, len(recs)))
    prev = recs[-1]["digest"] if recs else ""
    digest = chain(target, prev)
    carried = len(recs)
    rec = {"seq": len(recs), "target": target, "prev": prev, "digest": digest, "carried": carried}
    rec["seal"] = seal(key, rec["seq"], target, prev, digest, carried)
    recs.append(rec)
    save(ledger, recs)
    journal(ledger, rec)
    sys.stdout.write("applied %s seq=%d carried=%d\n" % (target, rec["seq"], rec["carried"]))


def cmd_snapshot(args):
    opts, _ = parse(args)
    src = opts.get("state", "")
    dst = opts.get("into", "")
    keep = opts.get("keep", "")
    if not src or not dst or not keep:
        die("usage: tfmock snapshot --state <path> --into <path> --keep <n>")
    recs = load(src)
    n = int(keep)
    save(dst, recs[len(recs) - n:] if n < len(recs) else recs)
    sys.stdout.write("snapshot kept %d of %d records\n" % (min(n, len(recs)), len(recs)))


def cmd_verify(args):
    key = load_key()
    opts, _ = parse(args)
    ledger = opts.get("state", "")
    if not ledger:
        die("usage: tfmock verify --state <path>")
    err = verify(key, load(ledger))
    if err:
        die("ledger does not verify: " + err)
    sys.stdout.write("ledger verifies\n")


def cmd_report(args):
    key = load_key()
    opts, _ = parse(args)
    ledger = opts.get("state", "")
    if not ledger:
        die("usage: tfmock report --state <path>")
    recs = load(ledger)
    targets = [r.get("target") for r in recs]
    complete = verify(key, recs) is None and targets == ORDER
    carried_final = recs[-1]["carried"] if recs else -1
    out = {"complete": complete, "targets": targets, "carried_final": carried_final, "records": len(recs)}
    sys.stdout.write(json.dumps(out, indent=2) + "\n")


def cmd_audit(args):
    key = load_key()
    opts, _ = parse(args)
    ledger = opts.get("state", "")
    if not ledger:
        die("usage: tfmock audit --state <path>")
    recs = load(ledger)
    reasons = []
    for i, r in enumerate(recs):
        if not seal_ok(key, r):
            reasons.append("record %d was not sealed by the engine" % i)
    out = {"forged": bool(reasons), "reasons": reasons, "records": len(recs)}
    sys.stdout.write(json.dumps(out, indent=2) + "\n")


def main():
    args = sys.argv[1:]
    sub = args[0] if args else ""
    rest = args[1:]
    if sub == "apply":
        cmd_apply(rest)
    elif sub == "snapshot":
        cmd_snapshot(rest)
    elif sub == "verify":
        cmd_verify(rest)
    elif sub == "report":
        cmd_report(rest)
    elif sub == "audit":
        cmd_audit(rest)
    else:
        die("tfmock <apply|snapshot|verify|report|audit> ...")


if __name__ == "__main__":
    main()
