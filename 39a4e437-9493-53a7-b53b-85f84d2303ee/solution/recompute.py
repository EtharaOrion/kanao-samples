#!/usr/bin/env python3
import hashlib
import json
import sys

ORDER = ["lint", "build", "test", "package", "publish"]


def chain(phase, prev):
    return hashlib.sha256((phase + "|" + prev).encode("utf-8")).hexdigest()


def verify(recs):
    prev = ""
    for i, r in enumerate(recs):
        if r.get("seq") != i:
            return "record %d carries seq %r" % (i, r.get("seq"))
        if r.get("phase") not in ORDER:
            return "record %d names unknown phase %r" % (i, r.get("phase"))
        if r.get("prev") != prev:
            return "record %d prev does not link" % i
        if r.get("digest") != chain(r.get("phase", ""), prev):
            return "record %d digest does not recompute" % i
        if r.get("carried") != i:
            return "record %d carried %r expected %d" % (i, r.get("carried"), i)
        prev = r["digest"]
    return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/workspace/state/carry.json"
    with open(path, "r", encoding="utf-8") as handle:
        recs = json.load(handle)
    err = verify(recs)
    complete = err is None and len(recs) == len(ORDER) and [r["phase"] for r in recs] == ORDER
    print(json.dumps({"complete": complete, "records": len(recs), "error": err}))
    sys.exit(0 if complete else 1)


if __name__ == "__main__":
    main()
