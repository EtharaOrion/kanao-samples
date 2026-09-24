#!/usr/bin/env python3
"""periodcheck - what the bureau confirms before it endorses a period.

`marrowfleet verify` walks the sheets the run staged and says whether a roster
exists for each base. That is not what the bureau is judged on. What a base
holds when the period closes is: a sheet for every crew member it rosters that
the period records a sector for, every sheet at the one bidding period, every
sheet settling under the published reader, and a rank it can crew for every rank
it is established for. This walks the drops themselves and says so.

Usage:  periodcheck [<register>]
Exit:   0 the period is complete, 1 the period is not, 2 usage
"""
import csv
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
READER = os.path.join(ROOT, "tools", "rostercheck", "rostercheck.py")


def conf(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            key, sep, value = line.partition(":")
            if sep:
                out[key.strip()] = value.strip()
    return out


def fragments(directory):
    out = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".toml"):
            continue
        frag = {}
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.partition("=")
                if sep:
                    frag[key.strip()] = value.strip().strip('"')
        out[frag["name"]] = frag
    return out


def declared(frag):
    return frag.get("enabled", "true") == "true"


def sheet_head(path):
    head = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            key, sep, value = line.partition(":")
            if sep:
                head[key.strip()] = value.strip()
    return head


def main(argv):
    if len(argv) > 1:
        sys.stderr.write("usage: periodcheck [<register>]\n")
        return 2
    register = argv[0] if argv else os.path.join(ROOT, "rostering", "rostering.yaml")
    home = os.path.dirname(register)
    reg = conf(register)
    crew = fragments(os.path.join(home, "crew.d"))
    bases = fragments(os.path.join(home, "bases.d"))

    with open(os.path.join(home, "sectors.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    period = max(row["period"] for row in rows)
    flown = {row["crew"] for row in rows if row["period"] == period}

    faults = []
    held_total = 0

    for name, frag in sorted(crew.items()):
        if declared(frag) and frag["base"] not in bases:
            faults.append("%s is rostered at %s, which the register does not "
                          "declare" % (name, frag["base"]))

    for base, frag in sorted(bases.items()):
        if not declared(frag):
            continue
        drop = os.path.join(reg["base_root"], base)
        if not os.path.isdir(drop):
            faults.append("%s has no drop at %s" % (base, drop))
            continue
        held = {name: sheet_head(os.path.join(drop, name))
                for name in sorted(os.listdir(drop)) if name.endswith(".sheet")}
        held_total += len(held)

        rosters = {n for n, f in crew.items()
                   if declared(f) and f["base"] == base}
        for member in sorted(rosters & flown):
            if not any(head.get("crew") == member for head in held.values()):
                faults.append("%s holds no sheet for %s, which it rosters and "
                              "which the period records a sector for"
                              % (base, member))

        for name, head in sorted(held.items()):
            if head.get("crew") not in rosters:
                faults.append("%s holds %s, a sheet for %s which it does not "
                              "roster" % (base, name, head.get("crew")))
            if head.get("period") != period:
                faults.append("%s holds %s at %s, not the %s the sectors "
                              "register is current at"
                              % (base, name, head.get("period"), period))
            settled = subprocess.run(
                [sys.executable, READER, os.path.join(drop, name),
                 reg["key_fingerprint"]], capture_output=True, text=True)
            if settled.returncode:
                faults.append("%s holds %s which the reader does not settle: %s"
                              % (base, name, settled.stderr.strip()))

        crewable = {head.get("rank") for head in held.values()}
        for rank in frag["ranks"].split(","):
            if rank in crewable:
                continue
            established = sorted(n for n in rosters if crew[n]["rank"] == rank)
            faults.append("%s cannot crew %s: %s" % (base, rank,
                          "no crew member is established in it" if not established
                          else "the period records no sector for %s"
                               % ", ".join(established)))

    print("period %s: %d bases, %d sheets held"
          % (period, sum(1 for f in bases.values() if declared(f)), held_total))
    for fault in faults:
        print("  not settled: %s" % fault)
    if faults:
        return 1
    print("  every base holds the period complete")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
