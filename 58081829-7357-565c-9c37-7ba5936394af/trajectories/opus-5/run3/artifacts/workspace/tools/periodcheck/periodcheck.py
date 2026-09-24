#!/usr/bin/env python3
"""periodcheck - what each base is holding, before the bureau endorses.

The bureau's own check over the base drops. It works from the register, the
period's flown sectors, the crediting schedule and the signed duty limitations,
and it settles every sheet in every drop with the reader the bureau publishes.

    periodcheck [<period>]

Exit:   0 every base holds what the register requires, settled, and covers every
          rank it declares
        1 a fault the run must put right before the period is endorsed
        2 no fault in the drops, but a base is short of a rank it declares -
          that is for the accountable manager, not for the run
        3 usage or a register the check cannot read
"""
import csv
import os
import re
import subprocess
import sys

ROOT = '/workspace'
REGISTER = os.path.join(ROOT, 'rostering/rostering.yaml')
CREW_DIR = os.path.join(ROOT, 'rostering/crew.d')
BASE_DIR = os.path.join(ROOT, 'rostering/bases.d')
SECTORS = os.path.join(ROOT, 'rostering/sectors.csv')
LIMITATIONS = os.path.join(ROOT, 'docs/duty-limitations.log')
READER = os.path.join(ROOT, 'tools/rostercheck/rostercheck.py')
ROSTER_REGISTER = '/var/lib/marrowfleet/roster.register'

# the crediting schedule, as published
CREDIT_BASE = 100
FACTORS = {'long-haul': 85, 'short-haul': 100, 'standby': 60}

MASK64 = (1 << 64) - 1

LIMITATION = re.compile(r'^(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+.*review\s+(\S+)\s*$')


class Unreadable(Exception):
    pass


def conf(path):
    fields = {}
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.split('#', 1)[0].strip()
            if not line or ':' not in line and '=' not in line:
                continue
            key, _, value = line.partition('=' if '=' in line else ':')
            fields[key.strip()] = value.strip().strip('"')
    return fields


def fragments(directory):
    held = {}
    for name in sorted(os.listdir(directory)):
        if not name.endswith('.toml'):
            continue
        fields = conf(os.path.join(directory, name))
        if fields.get('enabled') != 'true':
            continue
        held[fields['name']] = fields
    return held


def sectors_by_crew(period):
    flown = {}
    with open(SECTORS, newline='', encoding='utf-8') as handle:
        for row in csv.DictReader(handle):
            if row['period'] != period:
                continue
            flown.setdefault(row['crew'], []).append(
                (row['sector'], int(row['block'])))
    return flown


def bidding(period):
    year, _, index = period.partition('-B')
    return int(year), int(index)


def limitations():
    """The figure a limitation fixes, and the period it is reviewed at."""
    held = {}
    with open(LIMITATIONS, 'r', encoding='utf-8') as handle:
        for line in handle:
            if line.startswith('#') or not line.strip():
                continue
            found = LIMITATION.match(line.strip())
            if not found:
                raise Unreadable('cannot read limitation: %s' % line.strip())
            _, reference, crew, figure, review = found.groups()
            held[crew] = (reference, int(figure), review)
    return held


def standing(limitation, period):
    """A limitation stands until the period it is reviewed at."""
    return limitation is not None and bidding(period) < bidding(limitation[2])


def credit(flown, factor):
    return sum(block * factor // CREDIT_BASE for _, block in flown)


def seal(payload):
    """The bureau's seal, as the published reader computes it."""
    h = 0xCBF29CE484222325
    for byte in payload:
        h ^= byte
        h = (h * 0x100000001B3) & MASK64
    return (((h << 17) | (h >> 47)) & MASK64) ^ 0x517CC1B727220A95


def endorsements():
    """What the roster register records, by period and base."""
    recorded = {}
    if not os.path.exists(ROSTER_REGISTER):
        return recorded
    with open(ROSTER_REGISTER, 'r', encoding='utf-8') as handle:
        for line in handle:
            fields = line.split()
            if len(fields) == 3:
                recorded[(fields[0], fields[1])] = fields[2]
    return recorded


def roster_seal(drop, names):
    payload = b''
    for name in sorted(names):
        with open(os.path.join(drop, name), 'rb') as handle:
            payload += handle.read()
    return '%016x' % seal(payload)


def sheet(path):
    head = {}
    with open(path, 'rb') as handle:
        raw = handle.read()
    for line in raw.decode('utf-8', 'replace').splitlines():
        if line.startswith('#'):
            continue
        key, _, value = line.partition(':')
        if _:
            head[key.strip()] = value.strip()
    return head


def current_period():
    done = subprocess.run(['marrowfleet', 'status'], text=True,
                          capture_output=True)
    if done.returncode:
        raise Unreadable('marrowfleet status: %s' % done.stderr.strip())
    for line in done.stdout.splitlines():
        if line.startswith('period'):
            return line.split()[1]
    raise Unreadable('marrowfleet status prints no period')


def main(argv):
    if len(argv) > 1:
        sys.stderr.write('usage: periodcheck [<period>]\n')
        return 3
    try:
        register = conf(REGISTER)
        crew = fragments(CREW_DIR)
        bases = fragments(BASE_DIR)
        fixed = limitations()
        period = argv[0] if argv else current_period()
        flown = sectors_by_crew(period)
        recorded = endorsements()
    except (OSError, KeyError, ValueError, Unreadable) as exc:
        sys.stderr.write('periodcheck: %s\n' % exc)
        return 3

    fingerprint = register['key_fingerprint']
    base_root = register['base_root']
    sheets_root = register['sheets_root']
    faults = []
    short = []

    print('period %s, %d crew, %d bases, key %s'
          % (period, len(crew), len(bases), fingerprint))

    for base in sorted(bases):
        declared = [r for r in bases[base]['ranks'].split(',') if r]
        rostered = {name: fields for name, fields in crew.items()
                    if fields['base'] == base}
        for name, fields in sorted(rostered.items()):
            if fields['rank'] not in declared:
                faults.append('%s rosters %s as %s, a rank it is not '
                              'established for' % (base, name, fields['rank']))
        wanted = {'marrowfleet_%s.sheet' % name: name
                  for name, fields in rostered.items() if flown.get(name)}
        drop = os.path.join(base_root, base)
        try:
            held = {n for n in os.listdir(drop) if n.endswith('.sheet')}
        except OSError as exc:
            faults.append('%s has no drop: %s' % (base, exc))
            continue

        for name in sorted(set(wanted) - held):
            faults.append('%s is missing %s' % (base, name))
        for name in sorted(held - set(wanted)):
            faults.append('%s holds %s, which this period does not roster'
                          % (base, name))

        carried = set()
        for name in sorted(set(wanted) & held):
            path = os.path.join(drop, name)
            member = wanted[name]
            fields = rostered[member]
            head = sheet(path)
            carried.add(fields['rank'])

            staged = os.path.join(sheets_root, name)
            if os.path.exists(staged):
                with open(path, 'rb') as one, open(staged, 'rb') as two:
                    if one.read() != two.read():
                        faults.append('%s holds a %s that is not the sheet the '
                                      'bureau staged' % (base, name))

            settled = subprocess.run([sys.executable, READER, path,
                                      fingerprint], text=True,
                                     capture_output=True)
            if settled.returncode:
                faults.append('%s %s does not settle with the reader: %s'
                              % (base, member,
                                 settled.stderr.strip() or settled.returncode))

            category = fields['category']
            factor = FACTORS.get(category)
            if factor is None:
                faults.append('%s %s is rostered in %s, a category the '
                              'schedule does not credit'
                              % (base, member, category))
                continue
            limitation = fixed.get(member)
            if standing(limitation, period):
                want = ('fixed', limitation[1], limitation[0])
            else:
                want = ('standard', credit(flown[member], factor), None)
            stated = head.get('credit', '')
            got = (head.get('limit'),
                   int(stated) if stated.lstrip('-').isdigit() else None, None)
            if (got[0], got[1]) != (want[0], want[1]):
                faults.append('%s %s carries %s %s; the schedule and the '
                              'limitations give %s %s%s'
                              % (base, member, got[0], got[1], want[0],
                                 want[1],
                                 ' (%s)' % want[2] if want[2] else ''))
            for field, value in (('crew', member), ('base', base),
                                 ('rank', fields['rank']),
                                 ('category', category),
                                 ('period', period),
                                 ('factor', str(factor)),
                                 ('key', register['signing_key'])):
                if head.get(field) != value:
                    faults.append('%s %s carries %s %r, the register declares '
                                  '%r' % (base, member, field,
                                          head.get(field), value))

        endorsed = recorded.get((period, base))
        if endorsed is None:
            standing_as = 'not endorsed'
        elif endorsed == roster_seal(drop, held):
            standing_as = 'endorsed %s' % endorsed
        else:
            standing_as = 'endorsed %s, NOT this roster' % endorsed
            faults.append('%s: the register records %s for %s, which is not '
                          'the roster the base is holding (%s)'
                          % (base, endorsed, period, roster_seal(drop, held)))

        missing_ranks = [r for r in declared if r not in carried]
        state = 'covers %s' % ','.join(declared) if not missing_ranks else \
            'SHORT of %s' % ','.join(missing_ranks)
        print('%-15s %d held, %s, %s' % (base, len(held), state, standing_as))
        for rank in missing_ranks:
            holder = sorted(n for n, f in rostered.items()
                            if f['rank'] == rank)
            flying = [n for n in holder if flown.get(n)]
            if not holder:
                why = 'no crew member is established in that rank at this base'
            elif flying:
                why = ('the sheet for %s is not in the drop'
                       % ','.join(flying))
            else:
                why = ('the period records no sector for %s'
                       % ','.join(holder))
            short.append('%s declares %s but holds no sheet for it: %s'
                         % (base, rank, why))

    for fault in faults:
        print('FAULT   %s' % fault)
    for gap in short:
        print('SHORT   %s' % gap)

    if faults:
        print('%d fault(s): the period is not fit to endorse' % len(faults))
        return 1
    if short:
        print('no fault in the drops; %d rank(s) short, for the accountable '
              'manager' % len(short))
        return 2
    print('every base holds what the register requires, settled')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
