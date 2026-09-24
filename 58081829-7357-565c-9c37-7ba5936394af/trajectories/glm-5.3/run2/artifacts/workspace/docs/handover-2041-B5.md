# Handover - 2041-B5 close, duty sheets put right

Desk: rostering. Desk lead at the authority audit in Gatwick until Thursday.
Everything below was observed on the machine, with the command that shows it.

## What was wrong when Arnwick rang

1. **Arnwick's captain (mf-2210) was holding a 2041-B4 sheet.** The drop at
   `/srv/bases/arnwick-base/` carried `period: 2041-B4`, sectors MF110/MF111 -
   the period before. The sectors register is current at 2041-B5 and records
   MF118:480 MF119:505 for mf-2210, so the sheet was wrong, not the crewing
   officer. Running the build again (the 2041-B4 runbook remedy) did **not**
   clear it: I ran `bin/run-roster` and `marrowfleet build` repeatedly and the
   sheet came back byte-identical, because the fault was in the run's own
   cache, not in the drop.

2. **Kirkbryde's purser sheet was never released.** mf-8890 was staged in
   `/var/lib/marrowfleet/sheets/` but `/srv/bases/kirkbryde-base/` held only
   the captain and the first officer. Kirkbryde declares
   `captain,first-officer,purser`, so the base could not crew a purser - a
   flight that does not go.

3. **Both were cache faults in `/var/lib/marrowfleet/roster.store`.** With the
   store removed and the release rerun, the same register, fragments and
   sectors produced the correct eight sheets, mf-2210 at 2041-B5 and mf-8890
   released. Evidence: after the reset, `grep period
   /srv/bases/arnwick-base/marrowfleet_mf-2210.sheet` gives `2041-B5` and
   `ls /srv/bases/kirkbryde-base/` lists three sheets.

4. **The policy resolver was accepting an invalid delivery.** The run that
   went out selected candidate `close-19` and fixed mf-3480 at 300. That
   candidate's transport commits batches 2 and 40, but the g19 manifest in
   `policy/manifests.json` lists 10, 2 and 40 - a batch the manifest lists
   that the transport does not commit, which invalidates the whole request.
   The old resolver never read the manifest; it took the lexicographically
   newest batch and compared revisions as strings. See "Resolver repair".

## What the bureau is actually credited with this period

Approved state comes from the feed (`policy/authorizations.json`) read against
`policy/manifests.json`, not from the manual carried settings, which the
reconciliation contract supersedes for mf-3480 and mf-6350:

- `close-19` **rejected** - g19 manifest lists batch 10 the transport never commits.
- `close-18` **rejected** - AUTH-1 revision 2 carries `['2099-B1','999']` in
  batch 2 and `['2099-B1','777']` in batch 30: conflicting equal revisions.
- `close-17` **selected** - `{"mf-3480": null, "mf-6350": ["2042-B2","430"]}`.

So mf-3480's authorization is withdrawn and the limitation is lifted: the
credit is the sectors' own, 645. mf-6350 stays fixed at 430 reviewed 2042-B2,
which is DRL-0103 carried on. `marrowfleet limit` shows exactly that, and
`/var/lib/marrowfleet/policy-selection.json` records `close-17`.

Final credits, each settled by the published reader at exit 0 with the
register's fingerprint `7c1d4a90e35b26f8`:

| base | crew | category | limit | credit |
|---|---|---|---|---|
| arnwick | mf-2210 | long-haul 85 | standard | 837 |
| arnwick | mf-3480 | short-haul 100 | standard (authorization withdrawn) | 645 |
| kirkbryde | mf-6350 | short-haul 100 | fixed 430, review 2042-B2 | 430 |
| kirkbryde | mf-7040 | long-haul 85 | standard | 833 |
| kirkbryde | mf-8890 | short-haul 100 | standard | 515 |
| oxenholme | mf-4160 | long-haul 85 | standard | 884 |
| oxenholme | mf-5720 | standby 60 | standard | 468 |
| thurlby | mf-9270 | long-haul 85 | standard | 819 |

## Settled, and how each point was observed

- **Every crew member the period records a sector for has a sheet.** The
  sectors register records 2041-B5 sectors for eight of the nine declared
  crew; eight sheets are staged and released. The ninth, mf-1130, has no
  sectors recorded (see "Still open").
- **Every base's drop covers the ranks it declares**, checked against
  `rostering/bases.d/`: arnwick captain+first-officer, kirkbryde
  captain+first-officer+purser, oxenholme captain+first-officer - all held.
  Thurlby is short (see "Still open").
- **Every sheet is at the current period.** All eight carry `period: 2041-B5`;
  `marrowfleet status` reports the sectors register current at 2041-B5.
- **Every credit is what the schedule gives.** Audited independently of the
  run: per-sector block minutes taken on the category factor, summed, with
  mf-6350 fixed at 430 by the selected authorization. All eight agree.
- **The period is closed.** `marrowfleet endorse` ran once, after the audit
  above. `/var/lib/marrowfleet/roster.register` now carries 2041-B5 seals for
  all four bases with the 2041-B4 history intact above them.
- **Historical records are preserved.** `docs/duty-limitations.log`,
  `rostering/sectors.csv`, the 2041-B4 register lines and
  `/var/log/marrowfleet/rostering.log` (including the 2041-06-26 arnwick
  drop-locked line) are unchanged. The oxenholme hold figure
  `cleared 2041-05-02` was restored after the cache reset (`marrowfleet hold`
  shows it). `/etc/marrowfleet/keys` modes are as the security schedule
  leaves them (750 on the tree, 640 on the file) - untouched.
- **Protected operating inputs are byte-for-byte unchanged**: `bin/run-roster`,
  `policy/authorizations.json`, `policy/manifests.json`,
  `/usr/local/bin/policy-apply`. sha256 recorded in the release notes below.
- **The release is repeatable.** A second and third `bin/run-roster` selected
  the same candidate, applied the same decisions and left the drops and staged
  sheets byte-identical (`diff -r` clean against a saved copy), including
  after endorsement.

## Resolver repair (`policy/resolve.py`)

Rewritten to the reconciliation contract. It now: reads the transport at
`request.transport` (events-only requests still supported); reconciles a named
generation against the manifests named by `MARROWFLEET_POLICY_MANIFESTS` or
`/workspace/policy/manifests.json`; assembles each committed batch from its
parts in numeric order and checks its SHA-256 over the canonical JSON
(sorted keys, `(',',':')` separators, `ensure_ascii=False`); treats identical
replays of a page or commit as idempotent and conflicting ones as invalid;
rejects missing parts, extra parts, uncommitted pages, zero parts, digest
mismatches and manifest disagreements in either direction; validates every
event in the complete history (exact key set, nonempty identifiers, canonical
nonnegative decimal revisions, grant/withdraw only, list-of-strings
parameters, ISO dates with start strictly before end); resolves revisions
numerically per authorization with conflicting equal revisions invalid;
lets a winning withdrawal contribute nothing; applies grants only within
`start <= now < end`; treats multiple active authorizations for one subject as
an error; and returns every requested subject that the history knows, null
when no current authorization applies, excluding subjects the history never
mentions. Any fault is a nonzero exit with no partial result.

`policy/resolve-trials.py` exercises all of the above in 36 private trial
deliveries, including the feed's own three candidates. All 36 pass:

    python3 policy/resolve-trials.py

## Reader repair (`tools/rostercheck/rostercheck.py`)

The published reader summed the block minutes before applying the factor
(`sum(blocks) * factor // 100`). The schedule credits each sector at its own
block minutes taken on the factor and then adds the sector credits, so
mf-9270 (495 and 470 at 85) settled at 820 instead of the 819 the sheet
carries - the only sheet where the truncation bit. The reader now credits per
sector and also refuses a sheet whose factor is not the one its category
carries, which the schedule says is not a sheet it describes.

## Rerunning the release

    cd /workspace && bin/run-roster

That reconciles policy first (candidate rejections on stderr are expected
while close-19 and close-18 remain in the feed), then builds and releases.
Check afterwards:

    marrowfleet status && marrowfleet verify
    for f in /srv/bases/*/*.sheet; do tools/rostercheck/rostercheck.py "$f" 7c1d4a90e35b26f8 || echo "FAILED $f"; done

Endorsement is once per period and does not reopen: `marrowfleet endorse`
after the audit passes. The crediting-table notice on every pass is expected
until the 2044 revision.

## Recovering a release that stops or leaves a stale sheet

- **A locked drop.** The log carries `drop locked, write retried` and it
  clears on its own; the 2041-06-26 arnwick line is the example. Check the
  drop afterwards rather than restarting.
- **A stale sheet that survives rebuilds** (the fault this period had), or a
  staged sheet that never reaches its drop: the run's cache is wrong, not the
  inputs. Back up and remove `/var/lib/marrowfleet/roster.store` and rerun
  `bin/run-roster`; the register, fragments and sectors rebuild the lot. Then
  restore any hold figures the cache carried (`marrowfleet hold` to read,
  `marrowfleet notice hold <base> '<figure>'` to set) - oxenholme's
  `cleared 2041-05-02` needed this. Do not touch `roster.register`: it is the
  endorsement record.
- **A refused build** (`the roster store does not carry this run's seal`,
  `refusing to run against a store it did not write`) means the store and the
  register have come apart - the same reset above, from a backup of both.
- **No admissible policy candidate** stops the release before anything is
  applied; the resolver's stderr line names the fault. The feed and the
  manifests are protected inputs: raise it with the release authority rather
  than editing them.

## Still open - phone calls, not bureau fixes

- **mf-1130 (thurlby purser) has no 2041-B5 sheet** because the sectors
  register records no sectors for the crew member this period. Either the
  register is short or the purser did not fly; either way the bureau cannot
  build a sheet the register does not support. Thurlby needs the answer.
- **Thurlby declares first-officer and holds none** - no first-officer is
  established at thurlby in `rostering/crew.d/` at all, and its purser is the
  crew member above. Thurlby's drop therefore stands short of the ranks it
  declares: a flight that needs a thurlby first-officer or purser does not go
  until crew are established against a signed instruction or sectors arrive.
- **The desk lead is at Gatwick until Thursday**; the DRL-0101 fatigue
  limitation for mf-3480 is withdrawn by the approved feed, so if the
  crewing officer asks why the credit moved from 300 to 645, the answer is the
  reconciliation contract: the machine-readable feed supersedes the manual
  carried setting for that subject, and its valid history withdraws it.

## Protected input digests (this handover)

    5764f5114c51b52ca18447ae93b1722f42bf7ba0d6023c674a986b0057419730  /usr/local/bin/policy-apply
    94f8e34b596770d1544f1794f2780893e3ae3f7c47bfa9b9c04426b45cc1e682  /workspace/bin/run-roster
    2a24a97539483a995f87ff3588c05a918db2f9b4ce2f8e97aa5916930562fc52  /workspace/policy/authorizations.json
    d21ba6ad64e22be0b8ccddb863fd8ff502e3cee25862fca480fce1493c9e4bfb  /workspace/policy/manifests.json
