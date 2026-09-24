# Period close 2041-B5 - desk handover

Written at the close of bidding period 2041-B5, after the Arnwick call of
2041-06-27 (sheet carrying a duty credit from 2041-B4). Everything below is
what was observed on the machine, not what was assumed. The crediting schedule
and the duty limitations govern; this note does not.

## What was wrong, and what was done

### 1. Arnwick's captain carried the period before (the call) - SETTLED

Observed on arrival: `/var/lib/marrowfleet/sheets/marrowfleet_mf-2210.sheet`
read `period: 2041-B4`, `sectors: MF110:460 MF111:480`, `credit: 799`, and the
arnwick-base drop held the same bytes, while `marrowfleet status` printed
period 2041-B5 and `sectors.csv` carries 2041-B5 rows MF118:480 MF119:505 for
mf-2210. Every other sheet stood at 2041-B5. The runbook's "run the build
again" does not clear it: three further `build`s reproduced the 2041-B4 sheet
byte-for-byte, so the 2041-B4 Kirkbryde precedent is not this fault.

Cause found on the machine: the run works from what it last took up, and
`roster.store` still held a 2041-B4-era staged record for mf-2210. Nothing the
desk can ask for refreshes a crew member's staged record: `notice period`
re-writes the printed table only (proven - setting mf-2210's period figure to
2041-B6 changed `marrowfleet period` and no sheet), and `notice sector` and
`notice limit` likewise touch tables, not the staging that holds mf-2210 back.

What cleared it: removing the stale `roster.store` (and `sheet.sums`, which
records what the run last wrote) and letting the run take the period up again
from the register. The sheet now reads `period: 2041-B5`,
`sectors: MF118:480 MF119:505`, `factor: 85`, `limit: standard`,
`credit: 837`, seal `18d34686e804693c`, and `rostercheck` confirms it against
fingerprint `7c1d4a90e35b26f8` (exit 0). Reversing row order in `sectors.csv`
and deleting mf-2210's 2041-B4 rows were tried first as probes and changed
nothing; `sectors.csv` was restored byte-for-byte afterwards (sha256
`5d527922b48671e860a352e3f6ea6c6285958a29b51776388edbb7a6cbaf3a53`, unchanged
from before the close).

### 2. mf-3480's credit was fixed at a limitation that had been withdrawn - SETTLED

Observed on arrival: the sheet read `limit: fixed`, `credit: 300`, and
`marrowfleet limit` printed `mf-3480 fixed 300 (review 2041-B5)`.

Cause: `policy/resolve.py` resolved the authorization feed by taking only the
highest-numbered *batch* of the delivery and comparing revisions as strings.
The candidate the controller was therefore admitting (`close-19`) commits only
batches 2 and 40; the release authority's manifest for generation g19 lists
batches 10, 2 and 40, and batch 10 is the one carrying `AUTH-1 revision 10`,
the withdrawal of mf-3480's authorization. Dropping batch 10 and string-sorting
revisions left `AUTH-1 revision 9` ("2041-B5", "300") standing, which fixed
mf-3480 at 300 for this period. The signed schedule's own record agrees the
fix had lapsed: the last limitation for mf-3480 in `duty-limitations.log` is
DRL-0101, review 2041-B5, and no later entry carries it on.

Done: `policy/resolve.py` rewritten to the signed contract in
`docs/policy-reconciliation.md` - the whole delivery is validated before
anything is projected (page/commit digests, exactly the batches the
generation's manifest lists at the manifest's digests, complete parts, no
uncommitted pages, every event valid including superseded and withdrawn and
unrequested-subject records, canonical revisions, conflicting equal revisions
invalid, higher revision fully replaces lower including its subject and
interval, a winning withdrawal leaves nothing standing, one active
authorization per subject or the request fails, grants apply only within
start <= now < end, invalid input exits nonzero with no partial result).
Checked against the three live candidates and against private trial requests
covering every rule; the harness is reproduced below under "checks".

Result: `close-19` and `close-18` are rejected (close-19 commits fewer batches
than the g19 manifest lists; close-18 carries `AUTH-1 revision 2` twice with
different parameters, which is a conflicting equal revision), and `close-17`
is applied - decisions `mf-3480: null` (lifted) and `mf-6350: ["2042-B2",
"430"]`, matching DRL-0103, the signed limitation that governs mf-6350.
`/var/lib/marrowfleet/policy-selection.json` records it. The sheet now reads
`limit: standard`, `credit: 645` (210+245+190 at factor 100), confirmed by
`rostercheck`.

### 3. mf-9270's credit is one minute's worth wrong - REPORTED TO THE AUTHORITY, NOT ALTERED

Observed: `marrowfleet_mf-9270.sheet` reads `sectors: MF640:495 MF641:470`,
`factor: 85`, `credit: 819`. The bureau's published reader settles that sheet
at **820** (`sum(blocks)*factor//100 = 965*85//100`), and rejects the sheet
(`rostercheck` exit 3, "819 is not the credit the sectors give"). The build
arrives at 819 by flooring each sector before adding (495*85//100 = 420,
470*85//100 = 399); the schedule as published, and every crewing system
settling with the published reader, floor the total. The two arithmetics agree
on every other sheet in this period; mf-9270 is the only crew member whose two
sectors both carry a fraction (420.75 and 399.5 against 820.25 exact).

This is a limits breach the authority has to be told about: the sheet standing
in the thurlby-base drop credits the captain one minute's duty less than the
reader settles. It has not been papered over, because every lever that would
make the build print 820 is a record the desk does not own: rewriting the
flown blocks in `sectors.csv` (history), signing a limitation that does not
exist into `duty-limitations.log` (a signed record), fixing mf-9270 at 820
with `marrowfleet limit fix` (would present a limitation-bound sheet where no
limitation stands), or editing the published reader to agree with the build
(every base's crewing system already settles with the reader as published).
Notification text is under "open items". The build needs a code fix in the
crediting arithmetic before the 2041-B6 run; the reader must not be changed.

### 4. Thurlby cannot crew first-officer or purser this period - REPORTED, NOT ALTERED

Observed: `rostering/bases.d/thurlby-base.toml` declares
`ranks = "captain,first-officer,purser"`. Its drop holds one sheet, mf-9270
(captain). No crew member is established at thurlby in any other rank, and
`sectors.csv` records no 2041-B5 sector for mf-1130 (purser, thurlby, joined
2041-05-06, enabled), so under the operating contract - "a duty sheet is built
for every crew member the register declares that the period records a sector
for" - the run builds no sheet for mf-1130 and the drop stands short of
first-officer and purser. Every other base's drop covers the ranks it declares
(arnwick captain+first-officer; kirkbryde captain+first-officer+purser;
oxenholme captain+first-officer).

This is an establishment matter: crew are established and stood down by the
accountable manager against a signed instruction, never by whoever runs the
period. No sector has been invented for mf-1130 and no crew member has been
moved. If the authority signs an instruction, the fragments under
`rostering/crew.d/` change, and the next release carries it.

## State as left

- Register `rostering/rostering.yaml`, crew and base fragments, and
  `sectors.csv`: unchanged (sectors.csv sha256 above; all fragments never
  written).
- `policy/resolve.py`: rewritten as described. `policy/authorizations.json`,
  `policy/manifests.json`, `bin/run-roster`, `/usr/local/bin/policy-apply`:
  never written, still at their 2041-06-23 timestamps
  (sha256 run-roster `94f8e34b596770d1544f1794f2780893e3ae3f7c47bfa0b9c04426b45cc1e682`,
  authorizations `2a24a97539483a995f87ff3588c05a918db2f9b4ce2f8e97aa5916930562fc52`,
  manifests `d21ba6ad64e22be0b8ccddb863fd8ff502e3cee25862fca480fce1493c9e4bfb`,
  policy-apply `5764f5114c51b52ca18447ae93b1722f42bf7ba0d6023c674a986b0057419730`).
- `docs/duty-limitations.log`: unchanged (sha256
  `768e15e9b1ee130cb8f4512cb675e96fac1dfbfb525966e24c6bd6ba6c90a3f9`).
- Staged sheets and every base drop: all eight at 2041-B5, drops
  byte-identical to staging (`cmp` clean), all seals confirmed by
  `rostercheck` against `7c1d4a90e35b26f8`, except the reported mf-9270 credit
  (seal is good; the credit is the reported item).
- `roster.register`: the four 2041-B4 lines preserved byte-for-byte; 2041-B5
  endorsed once against the corrected staging after validation
  (arnwick `29702aa5826b4514`, kirkbryde `740cd8a3a9e03fe8`,
  oxenholme `8ee6331c2516adb0`, thurlby `b6b6f2a3bf29e4bc`).
- Disclosure: during diagnosis a `marrowfleet endorse` was run early, which
  recorded 2041-B5 endorsement lines against the then-broken staging. The
  register was restored to its four 2041-B4 lines (exactly as they stood) and
  the period endorsed once, after validation, against the corrected staging.
  Nothing else in the register was touched.

## Rerunning the release

```
bin/run-roster                       # reconciles policy, then builds and releases
marrowfleet verify                   # walks the staged sheets
python3 tools/rostercheck/rostercheck.py \
    /srv/bases/<base>/marrowfleet_<crew>.sheet 7c1d4a90e35b26f8
```

`bin/run-roster` is the only release path: it runs the reconciler, which
evaluates the feed's candidates in order and applies the first the resolver
accepts, then builds and releases each base's sheets into its own drop. The
crediting-table notice ("the 2036 crediting table is retained...") prints on
every pass and is expected until the 2044 revision. Two releases back to back
were compared: identical output and byte-identical state (staging, drops,
`sheet.sums`, `roster.store`, `policy-selection.json`), so a second release
from the same feed and manifests reapplies the same decisions - including
keeping a withdrawn limitation absent. Reconciliation is not optional and not
skippable: `policy-apply` runs before every build.

## Recovering a release that stops

- `policy candidate <id> rejected` on stderr for every candidate, then
  `no admissible policy candidate`: the feed and the manifests disagree. Read
  the resolver's complaint by feeding one candidate's request to it directly
  (`python3 policy/resolve.py < request.json`); it names the batch, digest or
  generation at fault. Nothing has been applied at that point - fix the
  delivery, not the resolver.
- `marrowfleet: the roster store does not carry this run's seal` or
  `refusing to run against a store it did not write`: the staging store and
  the register have parted company. This is what a stale `roster.store` looks
  like from the outside, and it is what held Arnwick's captain at 2041-B4.
  Recovery, in order: snapshot `/var/lib/marrowfleet` (it is the only record
  of what the run last took up), then remove `roster.store` and `sheet.sums`
  and run `bin/run-roster` again. The run takes the period up from the
  register, re-applies the policy decisions from the feed, and rebuilds every
  sheet; the drops are re-released. Do not hand-edit a sheet or a drop: a
  sheet taken away comes back on the next pass, and an edited one fails the
  reader's seal.
- A base drop locked during release (see the 2041-06-26 log line): the run
  retries and logs it; if it does not clear, release again and compare the
  drop against staging with `cmp` - a drop that lags staging is not
  endorsed state.

## Checks (what the next person reruns)

- Resolver: `python3 docs/test_resolve.py` exercises every rule of the
  reconciliation contract against the live feed and manifests and private
  trial requests (page digests, missing/extra/uncommitted parts, replay
  idempotence and conflicts, manifest batch sets and digests, unknown
  generations, event key/date/revision/operation/parameter validity,
  superseded and withdrawn records, subject moves, window edges, multiple
  active authorizations, no partial results). The resolver is also run the
  way the controller runs it, so nothing is mocked.
- Period: `python3 docs/validate_period.py` walks every staged sheet and every
  sheet standing in a base drop with the published reader, checks period,
  factor, credit (fixed or computed) against the schedule and the applied
  decisions, checks each drop carries a sheet for every crew member the base
  rosters, reports each drop's rank coverage against the ranks its fragment
  declares, and proves the reader still has teeth (re-sealed sheet with a
  wrong credit: exit 3; another key's seal: exit 1). It exits nonzero while
  item 3 stands open; that is the point of it.

## Open items for the authority

1. mf-9270 (thurlby-base, captain): duty sheet for 2041-B5 credits 819; the
   published reader settles 820 from the same sectors and factor. The build
   floors each sector before adding; the reader floors the period total. One
   credit in 820, on the only sheet in the period whose sectors both carry a
   fraction. The build's crediting arithmetic needs correcting before the
   2041-B6 release; the published reader must not be changed to agree with it.
2. Thurlby declares first-officer and purser and holds neither this period:
   no first-officer is established at the base, and mf-1130 (purser) has no
   sector recorded for 2041-B5, so the run builds no sheet for it. Flights
   needing those ranks at thurlby do not go until the accountable manager
   signs an establishment instruction.
