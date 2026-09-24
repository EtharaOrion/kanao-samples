#!/usr/bin/env python3
"""Independent settlement check of a released period.

Recomputes every duty sheet in every base drop straight from the register the
bureau holds (crew and base fragments, the sectors register, the crediting
schedule and the signed duty limitations) and compares it with what stands in
the drops. Deliberately does not import rostercheck, so the two are independent
opinions on the same sheets.

Usage: settle.py <period>
Exit: 0 settled, 1 a sheet is wrong or missing.
"""
import re
import sys
from pathlib import Path

WORKSPACE = Path('/workspace')
DROPS = Path('/srv/bases')
SCHEDULE = {'long-haul': 85, 'short-haul': 100, 'standby': 60}
CREDIT_BASE = 100


def fragments(directory):
    out = {}
    for path in sorted(directory.glob('*.toml')):
        record = {}
        for line in path.read_text().splitlines():
            match = re.match(r'\s*(\w+)\s*=\s*"?([^"]*?)"?\s*$', line)
            if match:
                record[match.group(1)] = match.group(2)
        out[record['name']] = record
    return out


def sectors_for(period):
    sectors = {}
    for row in (WORKSPACE / 'rostering/sectors.csv').read_text().splitlines()[1:]:
        if not row.strip():
            continue
        p, crew, code, minutes = row.split(',')
        if p == period:
            sectors.setdefault(crew, []).append((code, int(minutes)))
    return sectors


def limitations(period):
    """Latest signed limitation per crew that still stands at this period."""
    stands = {}
    for line in (WORKSPACE / 'docs/duty-limitations.log').read_text().splitlines():
        match = re.match(r'(\S+)\s+\S+\s+(\S+)\s+(\d+)\s+(.*?);\s+review\s+(\S+)', line)
        if not match:
            continue
        _date, crew, figure, _reason, review = match.groups()
        stands[crew] = (int(figure), review)
    # a limitation stands until the period it is reviewed at
    return {crew: fixed for crew, (fixed, review) in stands.items() if review > period}


def sheet_fields(path):
    fields = {}
    for line in path.read_text().splitlines():
        if line.startswith('seal:'):
            break
        key, _, value = line.partition(':')
        fields[key.strip()] = value.strip()
    sectors = []
    for leg in fields.get('sectors', '').split():
        code, _, minutes = leg.partition(':')
        sectors.append((code, int(minutes)))
    return fields, sectors


def main(period):
    crew = fragments(WORKSPACE / 'rostering/crew.d')
    bases = fragments(WORKSPACE / 'rostering/bases.d')
    sectors = sectors_for(period)
    fixed = limitations(period)

    problems = []
    print('period %s: %d crew declared, %d with sectors, %d limited (%s)'
          % (period, len(crew), len(sectors), len(fixed), ', '.join(sorted(fixed)) or 'none'))

    expected_sheets = {c: frag for c, frag in crew.items()
                       if frag.get('enabled') == 'true' and c in sectors}
    released = {}
    for drop in sorted(DROPS.iterdir()):
        for sheet in sorted(drop.glob('*.sheet')):
            fields, legs = sheet_fields(sheet)
            released[fields.get('crew')] = (drop.name, sheet, fields, legs)

    for name in sorted(expected_sheets):
        frag = crew[name]
        if name not in released:
            problems.append('%s: no sheet stands in any drop' % name)
            continue
        drop, path, fields, legs = released[name]
        if drop != frag['base']:
            problems.append('%s: released into %s but established at %s' % (name, drop, frag['base']))
        if fields.get('base') != frag['base'] or fields.get('rank') != frag['rank'] \
                or fields.get('category') != frag['category']:
            problems.append('%s: sheet header disagrees with the register' % name)
        if fields.get('period') != period:
            problems.append('%s: sheet carries period %s, not %s' % (name, fields.get('period'), period))
        if legs != sectors[name]:
            problems.append('%s: sectors %s, register has %s' % (name, legs, sectors[name]))
        factor = SCHEDULE[frag['category']]
        if int(fields.get('factor', 0)) != factor:
            problems.append('%s: factor %s, category %s is credited at %d'
                            % (name, fields.get('factor'), frag['category'], factor))
        if name in fixed:
            figure = fixed[name]
            if fields.get('limit') != 'fixed' or int(fields.get('credit', -1)) != figure:
                problems.append('%s: limitation stands at %d but sheet says %s/%s'
                                % (name, figure, fields.get('limit'), fields.get('credit')))
        else:
            credit = sum(m * factor // CREDIT_BASE for _, m in sectors[name])
            if fields.get('limit') != 'standard' or int(fields.get('credit', -1)) != credit:
                problems.append('%s: credit %s, sectors and factor give %d'
                                % (name, fields.get('credit'), credit))

    for name in sorted(released):
        if name not in expected_sheets:
            problems.append('%s: a sheet stands in %s but the period records no sector for them'
                            % (name, released[name][0]))

    print()
    for name in sorted(expected_sheets):
        drop, path, fields, legs = released.get(name, (None, None, {}, []))
        print('  %-9s %-14s %-14s %-8s %s credit %s'
              % (name, drop or '-', fields.get('rank', '-'), fields.get('period', '-'),
                 fields.get('limit', '-') + '/' + fields.get('category', '-'), fields.get('credit', '-')))

    print()
    for base_name, base in sorted(bases.items()):
        ranks = base['ranks'].split(',')
        held = {}
        for name, (drop, path, fields, legs) in released.items():
            if drop == base_name:
                held[fields.get('rank')] = held.get(fields.get('rank'), 0) + 1
        short = [r for r in ranks if r not in held]
        base_crew = [c for c in crew if crew[c]['base'] == base_name
                     and crew[c].get('enabled') == 'true']
        print('  %-14s declares %-32s holds %s' % (base_name, base['ranks'],
                                                   ', '.join('%s x%d' % kv for kv in sorted(held.items())) or 'nothing'))
        if short:
            for rank in short:
                holders = [c for c in base_crew if crew[c]['rank'] == rank]
                if holders:
                    unflown = [c for c in holders if c not in sectors]
                    problems.append('%s: short of %s; %s is established at this base but the period '
                                    'records no sector for them' % (base_name, rank, ', '.join(unflown) or '-'))
                else:
                    problems.append('%s: short of %s; no crew member is established at this base in that rank'
                                    % (base_name, rank))

    print()
    if problems:
        print('NOT SETTLED:')
        for problem in problems:
            print('  -', problem)
        return 1
    print('settled: every sheet the period owes is out, correct and credited')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else '2041-B5'))
