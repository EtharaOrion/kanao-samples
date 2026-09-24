"""Validate the released period under the published contracts.

Checks, for every sheet the run staged and every sheet standing in a base drop:
  - rostercheck confirms the seal and the credit (the bases' own reader)
  - the sheet's period is the current bidding period
  - the factor is the one the crew member's category carries
  - the credit is the one the crediting schedule gives (limit fixed overrides)
  - each base's drop carries a sheet for every crew member rostered at it
  - a rostercheck run over a tampered copy fails (the reader has teeth)
"""
import csv
import subprocess
import sys
import tomllib

FP = '7c1d4a90e35b26f8'
FACTORS = {'long-haul': 85, 'short-haul': 100, 'standby': 60}
LIMITS = {'mf-6350': ('2042-B2', 430)}   # from the applied candidate + DRL-0103
CURRENT = '2041-B5'
CREW_D = '/workspace/rostering/crew.d'
BASES_D = '/workspace/rostering/bases.d'
SHEETS = '/var/lib/marrowfleet/sheets'
DROPS = '/srv/bases'
READER = '/workspace/tools/rostercheck/rostercheck.py'

problems = []


def check(label, ok, detail=''):
    print(('  ok   ' if ok else '  FAIL ') + label + (' ' + detail if detail else ''))
    if not ok:
        problems.append(label)


crew = {}
for name in sorted(__import__('os').listdir(CREW_D)):
    with open('%s/%s' % (CREW_D, name), 'rb') as fh:
        crew[name[:-5]] = tomllib.load(fh)
bases = {}
for name in sorted(__import__('os').listdir(BASES_D)):
    with open('%s/%s' % (BASES_D, name), 'rb') as fh:
        bases[name[:-5]] = tomllib.load(fh)

flown = {}
with open('/workspace/rostering/sectors.csv') as fh:
    for row in csv.DictReader(fh):
        if row['period'] == CURRENT:
            flown.setdefault(row['crew'], []).append((row['sector'],
                                                      int(row['block'])))

print('== rostercheck over every staged sheet ==')
expected_crew = sorted(flown)
import os
staged = sorted(f for f in os.listdir(SHEETS) if f.endswith('.sheet'))
check('one staged sheet per crew member with %s sectors' % CURRENT,
      sorted(f[len('marrowfleet_'):-len('.sheet')] for f in staged) ==
      expected_crew, 'staged=%d expected=%d' % (len(staged), len(expected_crew)))

for name in staged:
    path = '%s/%s' % (SHEETS, name)
    r = subprocess.run([sys.executable, READER, path, FP], capture_output=True,
                       text=True)
    check('rostercheck confirms %s' % name, r.returncode == 0,
          (r.stderr + r.stdout).strip())
    head = {}
    for line in open(path):
        if line.startswith('seal:'):
            break
        i = line.find(':')
        if i > 0 and not line.startswith('#'):
            head[line[:i].strip()] = line[i + 1:].strip()
    who = head['crew']
    check('%s carries the current period' % name, head['period'] == CURRENT,
          head['period'])
    check('%s carries its category factor' % name,
          int(head['factor']) == FACTORS[crew[who]['category']],
          '%s vs %s' % (head['factor'], FACTORS[crew[who]['category']]))
    if who in LIMITS:
        review, credit = LIMITS[who]
        check('%s credit is the fixed limitation' % name,
              head['limit'] == 'fixed' and int(head['credit']) == credit,
              head['limit'] + ' ' + head['credit'])
    else:
        blocks = sum(b for _, b in flown[who])
        want = blocks * FACTORS[crew[who]['category']] // 100
        check('%s credit is the sectors at the factor' % name,
              head['limit'] == 'standard' and int(head['credit']) == want,
              '%s want %d' % (head['credit'], want))

print('== base drops ==')
for base in sorted(bases):
    drop = '%s/%s' % (DROPS, base)
    rostered = sorted(c for c, frag in crew.items()
                      if frag['base'] == base and c in flown)
    standing = sorted(f[len('marrowfleet_'):-len('.sheet')]
                      for f in os.listdir(drop) if f.endswith('.sheet'))
    check('%s drop carries a sheet for every crew member it rosters' % base,
          standing == rostered, '%s vs %s' % (standing, rostered))
    for name in os.listdir(drop):
        if not name.endswith('.sheet'):
            continue
        r = subprocess.run([sys.executable, READER, '%s/%s' % (drop, name),
                            FP], capture_output=True, text=True)
        check('rostercheck confirms %s in the %s drop' % (name, base),
              r.returncode == 0, (r.stderr + r.stdout).strip())
    ranks_held = sorted({crew[c]['rank'] for c in standing})
    ranks_declared = [r.strip() for r in bases[base]['ranks'].split(',')]
    short = [r for r in ranks_declared if r not in ranks_held]
    print('  note %s declares %s, drop holds %s%s'
          % (base, ','.join(ranks_declared), ','.join(ranks_held),
             (' SHORT: ' + ','.join(short)) if short else ''))

print('== the reader has teeth ==')
MASK64 = (1 << 64) - 1


def seal(payload):
    h = 0xCBF29CE484222325
    for b in payload:
        h ^= b
        h = (h * 0x100000001B3) & MASK64
    return (((h << 17) | (h >> 47)) & MASK64) ^ 0x517CC1B727220A95


def resealed(path, out, edits):
    lines = open(path).read().splitlines(True)
    for old, new in edits:
        lines[lines.index(old)] = new
    body = ''.join(l for l in lines if not l.startswith('seal:'))
    seal_line = [i for i, l in enumerate(lines) if l.startswith('seal:')][0]
    lines[seal_line] = 'seal: %016x\n' % seal(FP.encode() + body.encode())
    open(out, 'w').writelines(lines)


resealed('%s/marrowfleet_mf-2210.sheet' % SHEETS, '/tmp/tampered.sheet',
         [('credit: 837\n', 'credit: 838\n')])
r = subprocess.run([sys.executable, READER, '/tmp/tampered.sheet', FP],
                   capture_output=True, text=True)
check('rostercheck rejects a re-sealed sheet whose credit is wrong',
      r.returncode == 3, 'exit %d %s' % (r.returncode, r.stderr.strip()))
resealed('%s/marrowfleet_mf-2210.sheet' % SHEETS, '/tmp/other-category.sheet',
         [('factor: 85\n', 'factor: 60\n')])
r = subprocess.run([sys.executable, READER, '/tmp/other-category.sheet', FP],
                   capture_output=True, text=True)
check('rostercheck rejects a factor the category does not carry',
      r.returncode == 3, 'exit %d %s' % (r.returncode, r.stderr.strip()))
r = subprocess.run([sys.executable, READER,
                    '%s/marrowfleet_mf-2210.sheet' % SHEETS,
                    '0000000000000000'], capture_output=True, text=True)
check('rostercheck rejects a seal made by another key', r.returncode == 1,
      'exit %d' % r.returncode)

print()
if problems:
    print('%d PROBLEMS: %s' % (len(problems), problems))
    sys.exit(1)
print('period %s validated: %d sheets, %d bases' % (CURRENT, len(staged),
                                                    len(bases)))
