#!/usr/bin/env python3
"""closecheck - what the bases are actually holding as a bidding period closes.

`marrowfleet verify` only answers whether the bureau staged a roster against
each base. It cannot tell the desk that a sheet in a drop is a period stale,
that a drop is short of a rank its base is established for, or that a credit on
a sheet is not the credit the schedule gives. This walks the register, the flown
sectors, the crediting schedule and the duty limitations, and settles every
sheet that actually stands in a base's own drop with the published reader.

Usage:  closecheck [--period P] [--unendorsed] [--root DIR]
Exit:   0 the period is complete, 1 one or more faults, 2 usage
"""
import csv
import json
import os
import re
import subprocess
import sys
import tomllib

ROOT = "/workspace"
SELECTION = "/var/lib/marrowfleet/policy-selection.json"
REGISTER = "/var/lib/marrowfleet/roster.register"

FACTORS = {"long-haul": 85, "short-haul": 100, "standby": 60}
PERIOD_RE = re.compile(r"\A([0-9]{4})-B([0-9]+)\Z")


class Report:
    def __init__(self):
        self.faults = []

    def fault(self, text):
        self.faults.append(text)
        print("FAULT %s" % text)

    def note(self, text):
        print("note  %s" % text)

    def ok(self, text):
        print("ok    %s" % text)


def period_key(period):
    found = PERIOD_RE.match(period or "")
    return (int(found.group(1)), int(found.group(2))) if found else None


def read_conf(path):
    conf = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if ":" in line:
                name, _, value = line.partition(":")
                conf[name.strip()] = value.strip()
    return conf


def read_fragments(directory):
    found = {}
    for leaf in sorted(os.listdir(directory)):
        if leaf.endswith(".toml"):
            with open(os.path.join(directory, leaf), "rb") as handle:
                body = tomllib.load(handle)
            found[body["name"]] = body
    return found


def read_sectors(path):
    flown = {}
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            flown.setdefault(row["period"], {}).setdefault(
                row["crew"], []).append((row["sector"], int(row["block"])))
    return flown


def read_limitations(path):
    """Later entries govern for a crew member."""
    governing = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split(None, 4)
            if len(fields) < 5:
                continue
            _, reference, name, figure, rest = fields
            review = re.search(r"review\s+([0-9]{4}-B[0-9]+)", rest)
            governing[name] = {
                "reference": reference,
                "figure": int(figure),
                "review": review.group(1) if review else None,
            }
    return governing


def read_sheet(path):
    head = {}
    with open(path, "rb") as handle:
        raw = handle.read()
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("seal:"):
            head["seal"] = line[5:].strip()
            break
        cut = line.find(":")
        if cut > 0 and not line.startswith("#"):
            head[line[:cut].strip()] = line[cut + 1:].strip()
    return head


def current_period(root):
    printed = subprocess.run(["marrowfleet", "status"], cwd=root, text=True,
                             capture_output=True)
    for line in printed.stdout.splitlines():
        if line.startswith("period"):
            return line.split()[1]
    return None


def standing_limits(limitations, period, log, report):
    """The fixed duty credits that govern this period, feed over carried log."""
    now = period_key(period)
    expected = {}
    for name, held in sorted(limitations.items()):
        review = period_key(held["review"])
        if review is None:
            report.fault("limitation %s for %s carries no review period"
                         % (held["reference"], name))
        elif now < review:
            # A limitation stands until the period it is reviewed at.
            expected[name] = held["figure"]

    if not os.path.exists(SELECTION):
        report.fault("no policy selection was recorded, so the release did not "
                     "reconcile the signed authorization feed")
        return expected
    with open(SELECTION, "r", encoding="utf-8") as handle:
        selection = json.load(handle)
    report.ok("the release applied policy candidate %r from the signed feed"
              % selection.get("selected"))

    for name, parameters in sorted(selection.get("decisions", {}).items()):
        carried = expected.pop(name, None)
        if parameters is None:
            approved = None
        elif len(parameters) == 2 and period_key(parameters[0]):
            approved = (int(parameters[1])
                        if now < period_key(parameters[0]) else None)
        else:
            report.fault("the decision applied for %s is not a review period "
                         "and a figure: %r" % (name, parameters))
            continue
        if approved != carried:
            report.fault(
                "the decision applied for %s credits %r, but %s carries %r"
                % (name, approved, log, carried))
        if approved is not None:
            expected[name] = approved
    return expected


def check_register(bases, period, report):
    with open(REGISTER, "r", encoding="utf-8") as handle:
        recorded = {tuple(line.split()[:2]) for line in handle if line.strip()}
    for base in sorted(bases):
        if (period, base) not in recorded:
            report.fault("the roster register records no %s endorsement for %s"
                         % (period, base))
        else:
            report.ok("the roster register records the %s endorsement for %s"
                      % (period, base))


def check_fragments(crew, bases, report):
    for name, body in sorted(crew.items()):
        if body["base"] not in bases:
            report.fault("%s is rostered at %s, which the register does not "
                         "declare" % (name, body["base"]))
        elif body["rank"] not in bases[body["base"]]["ranks"].split(","):
            report.fault("%s is a %s at %s, which is not established for that "
                         "rank" % (name, body["rank"], body["base"]))
        if body["category"] not in FACTORS:
            report.fault("%s carries category %r, which the crediting schedule "
                         "does not price" % (name, body["category"]))


def check_sheet(leaf, path, name, body, period, sectors, fixed, conf, report):
    head = read_sheet(path)
    if head.get("period") != period:
        report.fault("%s carries the %s period, not %s"
                     % (leaf, head.get("period"), period))
    for field in ("crew", "base", "rank", "category"):
        want = name if field == "crew" else body[field]
        if head.get(field) != want:
            report.fault("%s carries %s %r, not %r"
                         % (leaf, field, head.get(field), want))
    factor = FACTORS.get(body["category"])
    if head.get("factor") != str(factor):
        report.fault("%s carries factor %r, but %s is credited at %s"
                     % (leaf, head.get("factor"), body["category"], factor))
    stated = " ".join("%s:%d" % leg for leg in sectors)
    if head.get("sectors") != stated:
        report.fault("%s carries sectors %r, not the %r the period records"
                     % (leaf, head.get("sectors"), stated))
    if fixed is None:
        want_limit = "standard"
        want_credit = sum(block * factor // int(conf["credit_base"])
                          for _, block in sectors)
    else:
        want_limit, want_credit = "fixed", fixed
    if head.get("limit") != want_limit:
        report.fault("%s is credited %r, but the schedule credits %r here"
                     % (leaf, head.get("limit"), want_limit))
    if head.get("credit") != str(want_credit):
        report.fault("%s carries credit %r, not the %d the schedule gives"
                     % (leaf, head.get("credit"), want_credit))
    if head.get("key") != conf["signing_key"]:
        report.fault("%s was sealed under key %r, not %r"
                     % (leaf, head.get("key"), conf["signing_key"]))


def check_drop(base, body, crew, due, sheets_root, base_root, conf, reader,
               report):
    drop = os.path.join(base_root, base)
    if not os.path.isdir(drop):
        report.fault("%s holds no drop at %s" % (base, drop))
        return
    held = {leaf for leaf in os.listdir(drop) if leaf.endswith(".sheet")}
    rostered = {name for name in due if crew[name]["base"] == base}
    owed = {"marrowfleet_%s.sheet" % name for name in rostered}
    for missing in sorted(owed - held):
        report.fault("%s was never released into the %s drop" % (missing, base))
    for extra in sorted(held - owed):
        report.fault("the %s drop holds %s, which that base does not roster "
                     "this period" % (base, extra))

    for rank in body["ranks"].split(","):
        crewed = sorted(name for name in rostered if crew[name]["rank"] == rank)
        absent = [name for name in crewed
                  if "marrowfleet_%s.sheet" % name not in held]
        if crewed and absent:
            report.fault("%s cannot crew %s: the drop holds no sheet for %s"
                         % (base, rank, ", ".join(absent)))
        elif crewed:
            report.ok("%s can crew %s from %s"
                      % (base, rank, ", ".join(crewed)))
        else:
            report.note("%s is established for %s but the register declares no "
                        "%s at that base with a sector this period"
                        % (base, rank, rank))

    for leaf in sorted(held):
        path = os.path.join(drop, leaf)
        settled = subprocess.run(
            [sys.executable, reader, path, conf["key_fingerprint"]],
            text=True, capture_output=True)
        if settled.returncode != 0:
            report.fault("%s in the %s drop does not settle with the published "
                         "reader: %s"
                         % (leaf, base, settled.stderr.strip()))
        else:
            report.ok("%s settles: %s" % (base, settled.stdout.strip()))
        staged = os.path.join(sheets_root, leaf)
        if os.path.exists(staged):
            with open(path, "rb") as one, open(staged, "rb") as two:
                if one.read() != two.read():
                    report.fault("the %s drop holds a %s that differs from the "
                                 "sheet the bureau staged" % (base, leaf))


def main(argv):
    root, period, endorsed = ROOT, None, True
    rest = list(argv)
    while rest:
        flag = rest.pop(0)
        if flag == "--period" and rest:
            period = rest.pop(0)
        elif flag == "--root" and rest:
            root = rest.pop(0)
        elif flag == "--unendorsed":
            endorsed = False
        else:
            sys.stderr.write("usage: closecheck [--period P] [--unendorsed] "
                             "[--root DIR]\n")
            return 2

    report = Report()
    rostering = os.path.join(root, "rostering")
    conf = read_conf(os.path.join(rostering, "rostering.yaml"))
    crew = read_fragments(os.path.join(rostering, "crew.d"))
    bases = read_fragments(os.path.join(rostering, "bases.d"))
    flown = read_sectors(os.path.join(rostering, "sectors.csv"))
    log = os.path.join(root, "docs", "duty-limitations.log")
    reader = os.path.join(root, "tools", "rostercheck", "rostercheck.py")
    sheets_root, base_root = conf["sheets_root"], conf["base_root"]

    period = period or current_period(root)
    if period_key(period) is None:
        report.fault("there is no bidding period to settle against")
        return 1
    report.ok("settling the %s period against %d crew and %d bases the "
              "register declares" % (period, len(crew), len(bases)))

    check_fragments(crew, bases, report)
    limits = standing_limits(read_limitations(log), period, log, report)
    sectors = flown.get(period, {})
    due = sorted(name for name in crew if name in sectors)
    for name in sorted(set(crew) - set(due)):
        report.note("%s (%s at %s) flew no sector in %s, so the period records "
                    "no duty sheet to credit"
                    % (name, crew[name]["rank"], crew[name]["base"], period))

    staged = {leaf for leaf in os.listdir(sheets_root)
              if leaf.endswith(".sheet")}
    owed = {"marrowfleet_%s.sheet" % name for name in due}
    for missing in sorted(owed - staged):
        report.fault("%s was never staged" % missing)
    for extra in sorted(staged - owed):
        report.fault("%s stands in the sheets root but the period records no "
                     "sheet for it" % extra)

    for name in due:
        leaf = "marrowfleet_%s.sheet" % name
        path = os.path.join(sheets_root, leaf)
        if os.path.exists(path):
            check_sheet(leaf, path, name, crew[name], period, sectors[name],
                        limits.get(name), conf, report)

    for base in sorted(bases):
        check_drop(base, bases[base], crew, due, sheets_root, base_root, conf,
                   reader, report)

    if endorsed:
        check_register(bases, period, report)

    print()
    if report.faults:
        print("%d fault(s): the %s period is not complete"
              % (len(report.faults), period))
        return 1
    print("the %s period is complete: every sheet the period records is "
          "staged, released into its base's drop and settles with the "
          "published reader" % period)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
