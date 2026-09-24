#!/usr/bin/env python3
"""periodcheck - what the bureau checks before it endorses a period.

It reads the register, the period's flown sectors, the crediting schedule, the
signed limitations and the signed authorization feed, and settles every duty
sheet the run staged and every sheet standing in a base's drop against them.

Two kinds of finding are reported separately:

  fault  the run has got something wrong and the bureau must put it right
         before the period is endorsed (exit 1)
  cover  a base cannot crew a rank it is established for; the register or the
         period's sectors are short and only the accountable manager can settle
         it, against a signed instruction (exit 2)

Usage:  periodcheck [--period 2041-B5]
Exit:   0 nothing found, 1 faults, 2 cover shortfalls only
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ROSTERCHECK = os.path.join(REPO, "tools", "rostercheck", "rostercheck.py")
REGISTER = os.path.join(REPO, "rostering", "rostering.yaml")
CREW_D = os.path.join(REPO, "rostering", "crew.d")
BASES_D = os.path.join(REPO, "rostering", "bases.d")
SECTORS = os.path.join(REPO, "rostering", "sectors.csv")
LIMITATIONS = os.path.join(REPO, "docs", "duty-limitations.log")
FEED = os.path.join(REPO, "policy", "authorizations.json")
RESOLVER = os.path.join(REPO, "policy", "resolve.py")
SELECTION = "/var/lib/marrowfleet/policy-selection.json"

FACTORS = {"long-haul": 85, "short-haul": 100, "standby": 60}
CREDIT_BASE = 100


def register():
    conf = {}
    with open(REGISTER, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if ":" in line:
                key, _, value = line.partition(":")
                conf[key.strip()] = value.strip()
    return conf


def fragments(directory):
    held = {}
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".toml"):
            continue
        fragment = {}
        with open(os.path.join(directory, entry), encoding="utf-8") as fh:
            for line in fh:
                match = re.match(r'\s*(\w+)\s*=\s*"?([^"\n]*?)"?\s*$', line)
                if match:
                    fragment[match.group(1)] = match.group(2)
        held[fragment["name"]] = fragment
    return held


def flown(period):
    """The period's sectors, in register order, by crew member."""
    held = {}
    with open(SECTORS, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["period"] == period:
                held.setdefault(row["crew"], []).append(
                    (row["sector"], int(row["block"])))
    return held


def credited(blocks, factor):
    """The schedule: each sector on the factor, the sectors added together."""
    return sum(block * factor // CREDIT_BASE for block in blocks)


def sheet(path):
    head = {}
    with open(path, "rb") as fh:
        raw = fh.read()
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            head[key.strip()] = value.strip()
    return head, raw


def marrowfleet(*args):
    run = subprocess.run(("marrowfleet",) + args, capture_output=True, text=True)
    if run.returncode:
        raise SystemExit("periodcheck: marrowfleet %s failed: %s"
                         % (" ".join(args), run.stderr.strip()))
    return run.stdout


def current_period():
    for line in marrowfleet("status").splitlines():
        if line.startswith("period"):
            return line.split()[1]
    raise SystemExit("periodcheck: marrowfleet status names no period")


def applied_limits():
    """What the run holds for each crew member: (credit, review) or None."""
    held = {}
    for line in marrowfleet("limit").splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == "fixed":
            held[parts[0]] = (int(parts[2]), parts[-1].rstrip(")"))
        elif len(parts) >= 2:
            held[parts[0]] = None
    return held


def signed_limitations():
    """The limitation standing against each crew member; later entries govern."""
    held = {}
    with open(LIMITATIONS, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            held[parts[2]] = (int(parts[3]), parts[-1])
    return held


def policy_decisions():
    """The candidate the resolver accepts now, and the decisions it carries."""
    with open(FEED, encoding="utf-8") as fh:
        feed = json.load(fh)
    for candidate in feed["candidates"]:
        run = subprocess.run([sys.executable, RESOLVER],
                             input=json.dumps(candidate["request"]),
                             capture_output=True, text=True)
        if run.returncode == 0:
            return feed["subjects"], candidate["id"], json.loads(run.stdout)
    return feed["subjects"], None, None


def check_policy(period, limits, faults):
    subjects, selected, decisions = policy_decisions()
    if selected is None:
        faults.append("no candidate in the signed feed is admissible")
    else:
        try:
            with open(SELECTION, encoding="utf-8") as fh:
                recorded = json.load(fh)
        except (OSError, ValueError):
            recorded = None
        if recorded != {"selected": selected, "decisions": decisions}:
            faults.append("the release recorded %r; the feed resolves to "
                          "candidate %s carrying %r"
                          % (recorded, selected, decisions))
        for subject, parameters in decisions.items():
            want = None if parameters is None else (int(parameters[1]),
                                                    parameters[0])
            if limits.get(subject) != want:
                faults.append("%s is held at %r; the signed feed authorizes %r"
                              % (subject, limits.get(subject), want))

    for member, limitation in signed_limitations().items():
        if member in subjects:
            continue
        if limits.get(member) != limitation:
            faults.append("%s is held at %r; the signed limitations log fixes "
                          "%r" % (member, limits.get(member), limitation))

    for member, limit in sorted(limits.items()):
        if limit is not None and limit[1] < period:
            faults.append("%s is held at a limitation reviewed at %s, a period "
                          "already past" % (member, limit[1]))


def check_sheets(period, conf, crew, bases, sectors, limits, faults, cover):
    for name, member in sorted(crew.items()):
        base = member["base"]
        if base not in bases:
            faults.append("%s rosters from %s, which the register does not "
                          "declare" % (name, base))
            continue
        staged = os.path.join(conf["sheets_root"], "marrowfleet_%s.sheet" % name)
        dropped = os.path.join(conf["base_root"], base,
                               "marrowfleet_%s.sheet" % name)
        if name not in sectors:
            cover.append("%s (%s, %s) flew no sector this period, so no sheet "
                         "is built for them" % (name, base, member["rank"]))
            if os.path.exists(staged) or os.path.exists(dropped):
                faults.append("%s flew no sector this period but a sheet "
                              "stands for them" % name)
            continue
        if not os.path.exists(staged):
            faults.append("%s has no staged sheet" % name)
            continue
        if not os.path.exists(dropped):
            faults.append("%s was not released into the %s drop" % (name, base))
            continue

        head, raw = sheet(staged)
        with open(dropped, "rb") as fh:
            if fh.read() != raw:
                faults.append("the %s drop holds a sheet for %s that is not "
                              "the one the bureau staged" % (base, name))

        for field, want in (("crew", name), ("base", base),
                            ("rank", member["rank"]),
                            ("category", member["category"]),
                            ("period", period),
                            ("key", conf["signing_key"])):
            if head.get(field) != want:
                faults.append("%s sheet carries %s %r; the register declares %r"
                              % (name, field, head.get(field), want))

        factor = FACTORS.get(member["category"])
        if head.get("factor") != str(factor):
            faults.append("%s sheet carries factor %r; its category is credited "
                          "at %r" % (name, head.get("factor"), factor))
        stated = " ".join("%s:%d" % leg for leg in sectors[name])
        if head.get("sectors") != stated:
            faults.append("%s sheet carries sectors %r; the period records %r"
                          % (name, head.get("sectors"), stated))

        limit = limits.get(name)
        if limit is None:
            if head.get("limit") != "standard":
                faults.append("%s sheet is marked %r; no limitation stands "
                              "against them" % (name, head.get("limit")))
            want = credited([block for _, block in sectors[name]], factor)
        else:
            if head.get("limit") != "fixed":
                faults.append("%s sheet is marked %r; a limitation fixes their "
                              "credit" % (name, head.get("limit")))
            want = limit[0]
        if head.get("credit") != str(want):
            faults.append("%s sheet credits %s; the schedule gives %s"
                          % (name, head.get("credit"), want))

        reader = subprocess.run(
            [sys.executable, ROSTERCHECK, dropped, conf["key_fingerprint"]],
            capture_output=True, text=True)
        if reader.returncode:
            faults.append("the reader does not settle the %s sheet in the %s "
                          "drop: %s" % (name, base, reader.stderr.strip()))


def check_drops(conf, crew, bases, faults, cover):
    for base, fragment in sorted(bases.items()):
        drop = os.path.join(conf["base_root"], base)
        if not os.path.isdir(drop):
            faults.append("%s holds no drop at %s" % (base, drop))
            continue
        held = set()
        for entry in sorted(os.listdir(drop)):
            match = re.match(r"marrowfleet_(.+)\.sheet\Z", entry)
            if not match:
                faults.append("the %s drop holds %s, which is not a duty sheet"
                              % (base, entry))
                continue
            member = crew.get(match.group(1))
            if member is None or member["base"] != base:
                faults.append("the %s drop holds a sheet for %s, whom it does "
                              "not roster" % (base, match.group(1)))
                continue
            held.add(member["rank"])
        for rank in fragment["ranks"].split(","):
            if rank not in held:
                cover.append("%s is established for %s and holds no %s sheet, "
                             "so it cannot crew that rank" % (base, rank, rank))


def check(period):
    faults = []
    cover = []
    conf = register()
    crew = {name: member for name, member in fragments(CREW_D).items()
            if member.get("enabled") == "true"}
    bases = {name: base for name, base in fragments(BASES_D).items()
             if base.get("enabled") == "true"}
    sectors = flown(period)
    limits = applied_limits()

    for name in sorted(set(sectors) - set(crew)):
        faults.append("the period records sectors for %s, whom the register "
                      "does not declare" % name)

    check_policy(period, limits, faults)
    check_sheets(period, conf, crew, bases, sectors, limits, faults, cover)
    check_drops(conf, crew, bases, faults, cover)
    return faults, cover


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period")
    args = parser.parse_args()
    period = args.period or current_period()
    faults, cover = check(period)
    print("periodcheck %s" % period)
    for line in faults:
        print("  fault: %s" % line)
    for line in cover:
        print("  cover: %s" % line)
    if not faults and not cover:
        print("  complete: every sheet stands in its base's drop and settles "
              "under the reader")
    if faults:
        return 1
    return 2 if cover else 0


sys.exit(main())
