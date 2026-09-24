#!/usr/bin/env python3
"""periodcheck - is this bidding period complete at the bases?

`marrowfleet verify` walks the sheets the run staged, and `marrowfleet endorse`
seals the sheets the run staged. Neither looks in a base's drop, so a drop that
is short of a sheet, or still holding a sheet from an earlier pass, passes both.
What the bureau is judged on is what a base is holding when the period closes,
so this reads the drops.

It settles every sheet in every drop with the published reader, recomputes each
duty credit from the register and the crediting schedule, checks each drop holds
exactly the sheets that base is owed, reports any rank a base cannot crew, and
recomputes the roster register's endorsement seals.

Two kinds of trouble are reported apart, because they are answered by different
people. A FINDING is the run's own fault and the period must not be closed on
it. A SHORTFALL is a gap in the register - a rank a base is established for that
no declared crew member can fill this period - which only the bureau's
accountable manager can close, against a signed instruction. Whoever is running
the period must escalate a shortfall, never paper over it by inventing a crew
fragment or a sector.

Usage:  periodcheck.py [--period 2041-B5]
Exit:   0 complete, 1 findings, 2 usage, 3 shortfalls only
"""
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
ROSTERING = os.path.join(REPO, 'rostering')
ROSTERCHECK = os.path.join(REPO, 'tools', 'rostercheck', 'rostercheck.py')
LIMITATIONS = os.path.join(REPO, 'docs', 'duty-limitations.log')
REGISTER = '/var/lib/marrowfleet/roster.register'
SUMS = '/var/lib/marrowfleet/sheet.sums'

FACTORS = {'long-haul': 85, 'short-haul': 100, 'standby': 60}
MASK64 = (1 << 64) - 1

FINDINGS = []
SHORTFALLS = []
NOTES = []


def finding(text):
    FINDINGS.append(text)


def shortfall(text):
    SHORTFALLS.append(text)


def note(text):
    NOTES.append(text)


def seal(payload):
    h = 0xCBF29CE484222325
    for byte in payload:
        h ^= byte
        h = (h * 0x100000001B3) & MASK64
    return (((h << 17) | (h >> 47)) & MASK64) ^ 0x517CC1B727220A95


def conf():
    out = {}
    with open(os.path.join(ROSTERING, 'rostering.yaml')) as handle:
        for line in handle:
            line = line.split('#')[0].strip()
            if ':' in line:
                key, _, value = line.partition(':')
                out[key.strip()] = value.strip()
    return out


def fragments(where):
    out = {}
    directory = os.path.join(ROSTERING, where)
    for name in sorted(os.listdir(directory)):
        if not name.endswith('.toml'):
            continue
        entry = {}
        with open(os.path.join(directory, name)) as handle:
            for line in handle:
                match = re.match(r'\s*([A-Za-z_]+)\s*=\s*(.+?)\s*$', line)
                if match:
                    entry[match.group(1)] = match.group(2).strip('"')
        out[entry['name']] = entry
    return out


def sectors():
    out = {}
    with open(os.path.join(ROSTERING, 'sectors.csv')) as handle:
        head = handle.readline()
        if head.strip() != 'period,crew,sector,block':
            finding('sectors.csv header is %r' % head.strip())
        for line in handle:
            line = line.strip()
            if not line:
                continue
            period, crew, sector, block = line.split(',')
            out.setdefault((period, crew), []).append((sector, int(block)))
    return out


def ordinal(period):
    match = re.match(r'^(\d{4})-B(\d+)$', period or '')
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def limitations():
    """The limitation standing against each crew member; later entries win."""
    out = {}
    with open(LIMITATIONS) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            match = re.match(r'^(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(.*?)'
                             r';\s*review\s+(\S+)\s*$', line)
            if not match:
                finding('duty-limitations.log line not understood: %r' % line)
                continue
            out[match.group(3)] = {'id': match.group(2),
                                   'figure': int(match.group(4)),
                                   'review': match.group(6)}
    return out


def stands(limitation, period):
    """A limitation stands until the period it is reviewed at."""
    if limitation is None:
        return False
    review, now = ordinal(limitation['review']), ordinal(period)
    if review is None or now is None:
        finding('cannot order period %r against review %r'
                % (period, limitation['review']))
        return False
    return now < review


def credited(blocks, factor):
    return sum(block * factor // 100 for block in blocks)


def table(name):
    out = subprocess.run(['marrowfleet', name], text=True, capture_output=True)
    if out.returncode:
        finding('marrowfleet %s exited %d' % (name, out.returncode))
    return out.stdout.splitlines()


def sheet_head(path):
    head = {}
    with open(path, 'rb') as handle:
        raw = handle.read()
    for line in raw.decode('utf-8', 'replace').splitlines():
        if line.startswith('#'):
            continue
        index = line.find(':')
        if index > 0:
            head[line[:index].strip()] = line[index + 1:].strip()
    return head, raw


def check_tables(period, crew, bases, limits):
    for line in table('period'):
        parts = line.split()
        if parts[0] == 'sectors':
            if parts[1] != period:
                finding('the sectors register is current at %s, not %s'
                        % (parts[1], period))
        elif parts[0] in crew and parts[1] != period:
            finding('the period table carries %s for %s, not %s'
                    % (parts[1], parts[0], period))
    for line in table('scope'):
        base, _, ranks = line.partition(' ')
        ranks = ranks.strip()
        if base in bases and ranks != bases[base]['ranks']:
            finding('the scope table carries %r for %s but its fragment '
                    'declares %r' % (ranks, base, bases[base]['ranks']))
    for line in table('hold'):
        base, _, held = line.partition(' ')
        held = held.strip()
        if base in bases and held != 'none':
            finding('%s carries a hold (%r); its drop will not be refreshed'
                    % (base, held))
    for line in table('limit'):
        parts = line.split(None, 1)
        if parts[0] not in crew:
            continue
        name, shown = parts[0], parts[1].strip()
        limitation = limits.get(name)
        if stands(limitation, period):
            want = 'fixed %d (review %s)' % (limitation['figure'],
                                             limitation['review'])
        else:
            want = 'standard'
        if shown != want:
            finding('the limit table carries %r for %s but the signed '
                    'limitations give %r' % (shown, name, want))


def check_staged(period, crew, registry, limits, flown, owed, sheets_root):
    staged = {}
    for name in sorted(owed):
        path = os.path.join(sheets_root, 'marrowfleet_%s.sheet' % name)
        if not os.path.exists(path):
            finding('no sheet was staged for %s' % name)
            continue
        staged[name] = path
    for entry in sorted(os.listdir(sheets_root)):
        match = re.match(r'^marrowfleet_(.+)\.sheet$', entry)
        if not match:
            finding('%s is not a duty sheet'
                    % os.path.join(sheets_root, entry))
        elif match.group(1) not in owed:
            finding('a sheet is staged for %s, which is owed none this period'
                    % match.group(1))

    recorded = {}
    if os.path.exists(SUMS):
        with open(SUMS) as handle:
            recorded = dict(line.split() for line in handle if line.strip())

    for name, path in sorted(staged.items()):
        head, raw = sheet_head(path)
        entry = crew[name]
        for field, want in (('crew', name), ('base', entry['base']),
                            ('rank', entry['rank']),
                            ('category', entry['category']),
                            ('period', period),
                            ('key', registry['signing_key'])):
            if head.get(field) != want:
                finding('%s: %s is %r, not %r'
                        % (name, field, head.get(field), want))
        factor = FACTORS.get(entry['category'])
        if factor is None:
            finding('%s: category %r has no crediting factor'
                    % (name, entry['category']))
            continue
        if head.get('factor') != str(factor):
            finding('%s: factor is %r but category %s is credited at %d'
                    % (name, head.get('factor'), entry['category'], factor))
        legs = flown[(period, name)]
        shown = ' '.join('%s:%d' % leg for leg in legs)
        if head.get('sectors') != shown:
            finding('%s: sectors are %r, not the %r the register records'
                    % (name, head.get('sectors'), shown))
        limitation = limits.get(name)
        if stands(limitation, period):
            want_limit, want_credit = 'fixed', limitation['figure']
            why = 'limitation %s' % limitation['id']
        else:
            want_limit = 'standard'
            want_credit = credited([block for _, block in legs], factor)
            why = 'the sectors at factor %d' % factor
        if head.get('limit') != want_limit:
            finding('%s: limit is %r, not %r'
                    % (name, head.get('limit'), want_limit))
        if head.get('credit') != str(want_credit):
            finding('%s: credit is %r but %s gives %d'
                    % (name, head.get('credit'), why, want_credit))
        summed = '%016x' % seal(raw)
        basename = os.path.basename(path)
        if recorded.get(basename) not in (None, summed):
            finding('%s: sheet.sums records %s but the staged sheet sums to %s'
                    % (name, recorded.get(basename), summed))
    return staged


def check_drops(crew, bases, declared, owed, staged, base_root, fingerprint):
    for base in sorted(bases):
        drop = os.path.join(base_root, base)
        if not os.path.isdir(drop):
            finding('%s has no drop at %s' % (base, drop))
            continue
        want = {'marrowfleet_%s.sheet' % name: name for name in sorted(owed)
                if crew[name]['base'] == base}
        for entry in sorted(os.listdir(drop)):
            if entry not in want:
                finding('%s is holding %s, which it is not owed this period'
                        % (base, entry))
        for entry, name in sorted(want.items()):
            path = os.path.join(drop, entry)
            if not os.path.exists(path):
                finding('%s is not holding a sheet for its %s %s'
                        % (base, crew[name]['rank'], name))
                continue
            with open(path, 'rb') as handle:
                dropped = handle.read()
            with open(staged[name], 'rb') as handle:
                if dropped != handle.read():
                    finding('%s is holding a sheet for %s that is not the one '
                            'the run staged' % (base, name))
            settled = subprocess.run(
                [sys.executable, ROSTERCHECK, path, fingerprint],
                text=True, capture_output=True)
            if settled.returncode:
                finding('the published reader rejects %s: %s'
                        % (path, settled.stderr.strip()))
        crewable = {crew[name]['rank'] for entry, name in want.items()
                    if os.path.exists(os.path.join(drop, entry))}
        for rank in [r for r in bases[base]['ranks'].split(',') if r]:
            if rank in crewable:
                continue
            based = sorted(name for name in declared
                           if crew[name]['base'] == base
                           and crew[name]['rank'] == rank)
            if not based:
                shortfall('%s is established for %s but the register declares '
                          'no %s at that base, so it cannot crew one'
                          % (base, rank, rank))
            elif not any(name in owed for name in based):
                shortfall('%s cannot crew %s: its only %s (%s) flew no sector '
                          'this period, so the period builds no sheet'
                          % (base, rank, rank, ', '.join(based)))
            else:
                finding('%s cannot crew %s: %s has a sheet this period but '
                        'none reached the drop'
                        % (base, rank, ', '.join(name for name in based
                                                 if name in owed)))


def check_register(period, crew, bases, owed, staged):
    lines = []
    if os.path.exists(REGISTER):
        with open(REGISTER) as handle:
            lines = [line.split() for line in handle if line.strip()]
    for base in sorted(bases):
        entries = [line for line in lines
                   if line[0] == period and line[1] == base]
        if not entries:
            finding('the roster register does not record %s against %s'
                    % (base, period))
            continue
        if len(entries) > 1:
            finding('the roster register records %s against %s %d times'
                    % (base, period, len(entries)))
        blob = b''
        for name in sorted(owed):
            if crew[name]['base'] == base and name in staged:
                with open(staged[name], 'rb') as handle:
                    blob += handle.read()
        want = '%016x' % seal(blob)
        if entries[0][2] != want:
            finding('the roster register endorses %s at %s against %s, but '
                    'the period now stands at %s'
                    % (base, period, entries[0][2], want))


def main(argv):
    period = None
    if argv[:1] == ['--period'] and len(argv) == 2:
        period = argv[1]
    elif argv:
        sys.stderr.write(__doc__)
        return 2

    registry = conf()
    sheets_root = registry.get('sheets_root', '/var/lib/marrowfleet/sheets')
    base_root = registry.get('base_root', '/srv/bases')
    fingerprint = registry['key_fingerprint']

    if period is None:
        status = subprocess.run(['marrowfleet', 'status'], text=True,
                                capture_output=True).stdout
        running = re.search(r'^period\s+(\S+)', status, re.M)
        if not running:
            finding('marrowfleet status does not report a period')
            print('FINDING: %s' % FINDINGS[0])
            return 1
        period = running.group(1)
    print('period under test: %s' % period)

    crew = fragments('crew.d')
    bases = fragments('bases.d')
    flown = sectors()
    limits = limitations()

    declared = {name for name, entry in crew.items()
                if entry.get('enabled', 'true') == 'true'}
    for name, entry in sorted(crew.items()):
        if entry.get('enabled', 'true') != 'true':
            note('%s is stood down in the register' % name)
        if entry['base'] not in bases:
            finding('%s is based at %s, which the register does not declare'
                    % (name, entry['base']))

    check_tables(period, crew, bases, limits)

    owed = {name for name in declared if (period, name) in flown}
    for name in sorted(declared - owed):
        note('%s (%s %s) flew no sector in %s, so the period builds no sheet '
             'for it' % (name, crew[name]['base'], crew[name]['rank'], period))

    staged = check_staged(period, crew, registry, limits, flown, owed,
                          sheets_root)
    check_drops(crew, bases, declared, owed, staged, base_root, fingerprint)
    check_register(period, crew, bases, owed, staged)

    print()
    for text in NOTES:
        print('note:      %s' % text)
    for text in SHORTFALLS:
        print('SHORTFALL: %s' % text)
    for text in FINDINGS:
        print('FINDING:   %s' % text)
    print()
    if FINDINGS:
        print('%d finding(s): the run is at fault and the period must not be '
              'closed on this state' % len(FINDINGS))
        return 1
    if SHORTFALLS:
        print('the run is sound: every sheet this period owes is in its base\'s '
              'drop and settles with the published reader.')
        print('%d shortfall(s) for the accountable manager: the register does '
              'not cover every rank its bases are established for.'
              % len(SHORTFALLS))
        return 3
    print('the period is complete at every base the register declares')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
