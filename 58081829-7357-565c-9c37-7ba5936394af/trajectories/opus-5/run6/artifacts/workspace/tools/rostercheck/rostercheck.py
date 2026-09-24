#!/usr/bin/env python3
"""rostercheck - the reader the bureau publishes for the bases' crewing systems.

It takes a duty sheet and the fingerprint the bureau holds its signing key
under, confirms the seal on the sheet was made by that key, and confirms the
duty credit is the one the sectors and the crediting factor give. A base's
crewing officer runs it over what stands in the base's own drop before a crew
member is rostered onto a flight.

The schedule is written out here as the bureau published it, so that a reader
built into a crewing system settles a sheet on its own.

Usage:  rostercheck <sheet> <fingerprint>
Exit:   0 confirmed, 1 seal, 2 usage, 3 credit
"""
import sys

MASK64 = (1 << 64) - 1

CREDIT_BASE = 100

FACTORS = {"long-haul": 85, "short-haul": 100, "standby": 60}


def seal(payload):
    h = 0xCBF29CE484222325
    for b in payload:
        h ^= b
        h = (h * 0x100000001B3) & MASK64
    return (((h << 17) | (h >> 47)) & MASK64) ^ 0x517CC1B727220A95


def sheet(path):
    head = {}
    body = []
    with open(path, "rb") as fh:
        raw = fh.read()
    for line in raw.decode("utf-8", "replace").splitlines(True):
        if line.startswith("seal:"):
            head["seal"] = line[5:].strip()
            break
        body.append(line)
        i = line.find(":")
        if i > 0 and not line.startswith("#"):
            head[line[:i].strip()] = line[i + 1:].strip()
    return head, "".join(body).encode("utf-8")


def flown(stated):
    out = []
    for leg in stated.split():
        code, _, minutes = leg.partition(":")
        if code and minutes.isdigit():
            out.append(int(minutes))
    return out


def credited(blocks, factor):
    return sum(block * factor // CREDIT_BASE for block in blocks)


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: rostercheck <sheet> <fingerprint>\n")
        return 2
    head, body = sheet(argv[0])
    stamped = "%016x" % seal(argv[1].encode("utf-8") + body)
    if head.get("seal") != stamped:
        sys.stderr.write("rostercheck: the seal was not made by that key\n")
        return 1
    factor = FACTORS.get(head.get("category"))
    if factor is None or head.get("factor") != str(factor):
        sys.stderr.write("rostercheck: the sheet does not carry the factor its "
                         "category is credited at\n")
        return 3
    if head.get("limit") == "standard":
        want = credited(flown(head.get("sectors", "")), factor)
        if int(head.get("credit", "-1")) != want:
            sys.stderr.write("rostercheck: %s is not the credit the sectors "
                             "give\n" % head.get("credit"))
            return 3
    sys.stdout.write("%s %s %s: %s credited\n"
                     % (head.get("base"), head.get("crew"), head.get("period"),
                        head.get("credit")))
    return 0


sys.exit(main(sys.argv[1:]))
