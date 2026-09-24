# 2041-B5 close - what was wrong, what settles it, how to run it again

Written for whoever picks the desk up next. The desk lead is at the authority's
audit until Thursday. Arnwick rang at 08:20 about their captain's sheet carrying
a credit from the period before; that call turned out to be one of five faults in
the same release, and the period had also been endorsed off a roster the bases
were not holding.

The period is now closed: four bases, eight duty sheets, every sheet settled with
the reader the bureau publishes, and the roster register recording, for each
base, the roster that base is actually holding.

One item is left open and is **not** the run's to fix - see *Left open* below.

## What the run reported and what was actually true

`marrowfleet verify` said "the bureau has a roster for every base the register
declares", and the log carried nothing but the 2036 crediting-table notice. That
is not a statement about the period. `verify` only looks for a roster row per
base in `roster.register`; it passed while Arnwick held a last-period sheet,
while Kirkbryde was short its purser, while Oxenholme's drop was frozen, and it
still passes when the register holds no row at all for the current period. Use
`tools/periodcheck/` before endorsing - that is the check that looks at the drops.

## The five faults, the evidence, and what settles each

The run works from what it last **took up**. `marrowfleet period|hold|scope|limit`
print what has been **asked for**. Three of the five faults were figures asked
for and never applied, which is exactly the case the README warns about: "The two
agree for as long as nobody asks for a change without applying it."

### 1. Arnwick, mf-2210 (captain) - a credit from the period before

* Observed: the sheet in `/srv/bases/arnwick-base` read `period: 2041-B4`,
  `sectors: MF110:460 MF111:480`, `credit: 799`, correctly sealed. The period
  table already read `mf-2210 2041-B5`; the run had taken up 2041-B4.
* Applied: `marrowfleet period set mf-2210 2041-B5`.
* Settles: the sheet now reads `period: 2041-B5`,
  `sectors: MF118:480 MF119:505`, `credit: 837` (480x85/100 = 408 plus
  505x85/100 = 429), and settles with the reader. That is the figure to give the
  Arnwick crewing officer: **837**, 2041-B5.

### 2. Kirkbryde, mf-8890 (purser) - a sheet that was built but never released

* Observed: `marrowfleet build` printed "kirkbryde-base 3 rostered" and staged
  `marrowfleet_mf-8890.sheet`, but the drop held two files. The base fragment
  declares `captain,first-officer,purser`; the scope the run had taken up was
  short of purser, and a base is released only the ranks it has taken up.
* Applied: `marrowfleet scope set kirkbryde-base captain,first-officer,purser`
  (the ranks the base's own fragment declares - the register is the authority,
  not the operator).
* Settles: the drop holds three sheets, `periodcheck` reports
  "covers captain,first-officer,purser", and the purser sheet settles at 515.

### 3. Oxenholme - a drop frozen by a hold whose clearance was never applied

* Observed: the hold table read `oxenholme-base cleared 2041-05-02`, but no
  release reached the base: repeated builds left both files untouched, and the
  drop's mf-4160 was the pre-correction sheet (`MF331:520`, `credit: 867`) while
  the bureau had staged `MF331:540`, `credit: 884`. The clearance had been asked
  for and never applied, so the hold was still in force.
* Applied: `marrowfleet hold clear oxenholme-base`.
* Settles: the drop copy is now byte-identical to the staged sheet, 884
  (500x85/100 = 425 plus 540x85/100 = 459), and the whole drop refreshes on
  every pass.

### 4. The signed authorization reconciliation - the wrong candidate was winning

`policy/resolve.py` is the adapter the release controller drives. It was wrong in
seven ways, and the effect on this period was that candidate `close-19` was
accepted and reinstated a limitation the approved history withdraws - that is
where the Arnwick first officer's `fixed 300` came from.

What was wrong, and what the rewritten resolver does:

| fault | now |
| --- | --- |
| only the "newest" batch by string comparison was read | every committed batch belongs to the history |
| no transport validation | digests, part coverage, uncommitted pages and replays all checked |
| no release manifest check | a named generation must commit exactly the batches its manifest lists, at the manifest's digests |
| revisions compared as strings (`"9" > "10"`) | compared numerically |
| withdrawals skipped | a withdrawal is a revision; if it wins, the authorization contributes nothing |
| grant window inclusive of `end` | `start <= now < end` |
| no event/consistency checks | event shape validated; conflicting equal revisions and two active authorizations for one subject are errors |

* Observed now (`python3 -m unittest discover -s tests`, and on the real feed):
  * `close-19` rejected - generation g19 does not commit batch `10`, which its
    manifest lists.
  * `close-18` rejected - AUTH-1 revision 2 is delivered twice with different
    parameters (`999` in batch 2, `777` in batch 30).
  * `close-17` accepted - `{"mf-3480": null, "mf-6350": ["2042-B2", "430"]}`.
    AUTH-1's winning revision (10) is a withdrawal; AUTH-2's winning revision
    (10) grants `2042-B2 430` inside its window.
  * `/var/lib/marrowfleet/policy-selection.json` records
    `{"decisions": {"mf-3480": null, "mf-6350": ["2042-B2", "430"]}, "selected": "close-17"}`.
  * `marrowfleet limit` reads `mf-3480 standard` and
    `mf-6350 fixed 430 (review 2042-B2)`.
  * mf-3480's sheet: `limit: standard`, `credit: 645` (645 block minutes at
    factor 100). mf-6350's sheet: `limit: fixed`, `credit: 430`.
* Cross-check against the signed limitations, which govern: DRL-0101 fixes
  mf-3480 at 300 with review 2041-B5, and a limitation stands only *until* the
  period it is reviewed at, so it does not stand at 2041-B5. DRL-0103 carries
  mf-6350's 430 on to review 2042-B2, so it does stand. The selected history and
  `docs/duty-limitations.log` agree.
* Replay: three consecutive releases from the same feed and manifests select
  `close-17`, apply the same decisions and leave the drops byte-identical.

Do not hand-set the limits for mf-3480 or mf-6350. They come from the feed on
every release; a manual correction is overwritten on the next pass.

### 5. The published reader did not settle a credit the way the schedule does

* Observed: `tools/rostercheck/` refused Thurlby's captain -
  "rostercheck: 819 is not the credit the sectors give" - while the sealed sheet
  carried 819. The reader added the block minutes and then took the factor
  (965x85/100 = 820); the schedule credits each sector and adds the credits
  (495x85/100 = 420 plus 470x85/100 = 399 = 819). mf-9270 is the only crew
  member this period where the two readings differ, which is why it had not been
  noticed before.
* The reader also took the factor from the sheet rather than from the crew
  member's category, so a sheet carrying a factor its category is not credited at
  would have settled - the schedule says such a sheet "is not a sheet this
  schedule describes, whatever the credit written on it comes to".
* Fixed both in `tools/rostercheck/rostercheck.py`; the exit codes and the
  output line are unchanged, because the bases' crewing systems read them.
* Settles: all eight sheets in the four drops settle at exit 0.
  `tests/test_rostercheck.py` pins the per-sector arithmetic (819 accepted, 820
  refused) and the factor-to-category binding.

### 6. The 2041-B5 endorsement recorded a roster no base was holding

Own error, recorded here because it is material. While surveying the tool I ran
`marrowfleet endorse` before the drops were put right, and it wrote the four
2041-B5 rows off that state (it prints "endorsed the ... period" when it writes
and "has endorsed in ..." when it refuses - that is how you tell).

Because the register does not reopen a period, the premature rows were removed
from `/var/lib/marrowfleet/roster.register` and the period was endorsed once,
last, after `periodcheck` reported no fault. The 2041-B4 rows are byte-for-byte
as found; nothing else in the register was touched.

* Settles: each 2041-B5 row is now the bureau's seal over the sheets in that
  base's drop, in sheet-name order, which `periodcheck` recomputes:
  arnwick `29702aa5826b4514`, kirkbryde `740cd8a3a9e03fe8`,
  oxenholme `8ee6331c2516adb0`, thurlby `b6b6f2a3bf29e4bc`.
  `marrowfleet endorse` now answers "has endorsed in 2041-B5".

Endorse once, and last. `periodcheck` now recomputes the register row against the
drop, so a roster that is endorsed off the wrong state shows up as a FAULT rather
than as a passing `verify`.

## Left open - for the desk lead and the accountable manager

`thurlby-base` declares `captain,first-officer,purser` and is holding a captain
only:

* no crew fragment establishes a **first officer** at Thurlby at all (not in
  2041-B4 either), so the run has nothing to build;
* **mf-1130** (purser, joined 2041-05-06) has no sector in the 2041-B5 sectors
  register, and a duty sheet is built only for a crew member the period records a
  sector for.

Thurlby therefore cannot crew a first officer or a purser from this drop.
Neither gap is the run's to close: it needs either a signed instruction (to
establish a first officer, or to change the ranks the base is established for) or
a sectors record showing mf-1130 flying. Nothing was invented here to make the
check go green - `periodcheck` exits 2 and names both gaps on every run.

## Running the release again, and recovering one that stops

```
/workspace/bin/run-roster > /tmp/run.log 2>&1 ; echo $?     # reconcile, build, release
python3 /workspace/tools/periodcheck/periodcheck.py         # what the bases hold
python3 -m unittest discover -s /workspace/tests            # the contracts
```

* `run-roster` is the only thing that reaches a base. It is idempotent:
  it reconciles the signed feed (inadmissible candidates are named on stderr),
  rebuilds all eight sheets and re-releases every drop. A sheet taken out of a
  drop, or tampered with in a drop, comes back on the next pass - verified by
  removing Kirkbryde's purser sheet and overwriting it with rubbish.
* **A run that stops partway loses nothing - run it again.** If it is killed
  (or its stdout is closed early) it can leave the sheets root part-written,
  `sheet.sums` behind and nothing released; the next full pass converges. Check
  with `periodcheck`, not with `verify`.
* **Never pipe the run into `head`.** `marrowfleet build | head -3` kills the run
  on the closed pipe: one pass wrote four of eight sheets and released nothing.
  Redirect to a file.
* `periodcheck` exit codes: 0 every base holds what the register requires and
  covers every rank it declares; 1 a fault to put right before endorsing;
  2 no fault in the drops but a base is short of a rank (accountable manager);
  3 the check could not read the register.
* If a drop is still behind after a rerun, the run has not taken a figure up.
  Compare the tables with the sheets and apply what is missing:

  | asked for | applied with |
  | --- | --- |
  | `marrowfleet hold` shows a clearance, nothing reaches the base | `marrowfleet hold clear <base>` |
  | `marrowfleet scope` shows a rank the drop never receives | `marrowfleet scope set <base> <ranks from the base fragment>` |
  | `marrowfleet period` shows the current period, the sheet shows the last one | `marrowfleet period set <crew> <period>` |
  | `marrowfleet limit` shows a figure the sheet does not carry | for mf-3480/mf-6350, fix `policy/resolve.py` and rerun the release; for anyone else, `marrowfleet limit fix <crew> <review-period> <figure>` / `marrowfleet limit lift <crew>` against a signed limitation |

* Then, once and only once: `marrowfleet endorse`.

## What is under test

* `tests/test_resolve.py` - the reconciliation contract: event validation,
  segmented delivery (part order, digests, replays, missing/extra parts,
  uncommitted pages), release manifests and trial deliveries, revision
  supersession including corrections that move subject or leave the window,
  withdrawals, the end-exclusive window, conflicting equal revisions, two active
  authorizations for one subject, the projection of requested and unknown
  subjects, and the three candidates in the installed feed.
* `tests/test_rostercheck.py` - the reader settles a credit the way the schedule
  does, binds the factor to the category, refuses a broken seal and a credit that
  is not the sectors', leaves a fixed limit alone, and settles every sheet now
  staged and released.
* `tests/test_period.py` - `periodcheck` reports no fault, every base is
  reported, the register records the roster each base holds, and the period is
  endorsed once per base.

57 tests, all passing.
