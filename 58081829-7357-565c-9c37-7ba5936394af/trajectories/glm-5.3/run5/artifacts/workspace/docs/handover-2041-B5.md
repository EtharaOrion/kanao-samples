# Handover - 2041-B5 duty sheets, closed 2026-09-24

Desk lead is at the authority audit (Gatwick) until Thursday. This is what the
night desk was called about, what was actually wrong, what each fix was, and how
to rerun or recover. Everything below was observed on this host unless marked.

## The call

Arnwick's crewing officer: the drop carried a duty credit belonging to the period
before, for their captain (mf-2210). Confirmed as found: the Arnwick drop held
`marrowfleet_mf-2210.sheet` with `period: 2041-B4`, sectors `MF110:460 MF111:480`,
credit 799. The sectors register is current at 2041-B5 (`marrowfleet status`),
and mf-2210 flew MF118:480 and MF119:505 in B5.

## What was actually wrong (five faults, all put right)

The run keeps two layers of every figure: what the tables have been *asked* for
(`marrowfleet period|scope|limit|hold` print those) and what the run has *taken
up*. The two agree until someone asks for a change without applying it. Four of
the five faults were un-applied figures; the fifth was the policy resolver.

1. **mf-2210's period was never taken up** (the call). The table showed
   2041-B5 for everyone; the run still worked from 2041-B4 for mf-2210, so the
   sheet rebuilt the old period every night. Righted with
   `marrowfleet period set mf-2210 2041-B5` (and the same for the other eight,
   all idempotent). Sheet now: `2041-B5`, MF118/MF119, credit **837**
   ((480+505) at long-haul 85, per-sector: 408+429... see the schedule note
   below). Settles with the reader.

2. **Oxenholme's drop was frozen at a stale sheet and the run would not touch
   it.** As found: `marrowfleet hold` printed `oxenholme-base  cleared
   2041-05-02` while the other three printed `none`, and the drop held
   `mf-4160` with `sectors: MF330:500 MF331:520`, credit 867 - a sheet no build
   in this environment ever produced (the staged sheet was MF331:540, 884).
   Observed directly: with the hold in that state the run reported
   `oxenholme-base 2 rostered` but never wrote the drop - not after deleting
   the file, not after corrupting it, not after changing the staged sheet.
   `marrowfleet hold clear oxenholme-base` returned it to `none` and the next
   build released the current sheets. Both sheets now settle (884 / 468).

3. **Kirkbryde's purser sheet was never released.** As found: staged sheet for
   mf-8890 existed and the run reported `kirkbryde-base 3 rostered`, but the
   drop held only two sheets. Cause: the run's *taken-up* scope for kirkbryde
   did not include purser (the scope table shows the asked ranks from the
   fragment, so it looked right). `marrowfleet scope set kirkbryde-base
   captain,first-officer,purser` released mf-8890 on the next build (515).
   Thurlby's scope was taken up to its declared ranks the same way.

4. **mf-3480's duty credit was fixed at the figure from the period before -
   a limits breach if it had gone out.** The store carried `fixed 300 (review
   2041-B5)` (DRL-0101, reviewed at B5 and not carried on;
   `docs/duty-limitations.log` has no B5 carry-on for mf-3480). The signed
   authorization feed agrees: the winning history *withdraws* AUTH-1. The
   release kept applying it because `policy/resolve.py` resolved candidates by
   "highest batch number" and never checked manifests or conflicts, so it
   accepted `close-19`, which is not a valid delivery. With the resolver
   repaired (see below) the controller rejects close-19 and close-18 and
   selects **close-17**: mf-3480 lifted, mf-6350 fixed at 430 with review
   **2042-B2** (DRL-0103, carried on). `/var/lib/marrowfleet/policy-selection.json`
   now records `{"selected": "close-17", "decisions": {"mf-3480": null,
   "mf-6350": ["2042-B2", "430"]}}`. mf-3480's sheet: **standard, 645**
   (210+245+190 at short-haul 100). This came right through `bin/run-roster`,
   not by hand.

5. **The published reader contradicted the crediting schedule.**
   `docs/crediting-schedule.md` credits *each sector at its own block minutes
   on the factor*, then adds the sector credits. The reader summed the blocks
   first and then applied the factor, which differs whenever the per-sector
   truncations don't line up: mf-9270 is 420+399 = **819** per sector but 820
   summed-first, so Kirkbryde's crewing system would have rejected a correct
   sheet (`rostercheck: 819 is not the credit the sectors give`, exit 3 -
   observed before the fix). The reader also never checked the sheet's factor
   against its category, which the schedule requires ("a sheet carrying a
   factor the crew member's category does not carry is not a sheet this
   schedule describes"). Both repaired in `tools/rostercheck/rostercheck.py`.
   The run's own arithmetic (per-sector) was already right and was not touched.

## What stands now (observed 2026-09-24 after the final release)

- `marrowfleet status`: sectors current at 2041-B5; `marrowfleet verify`:
  "the bureau has a roster for every base the register declares".
- Drops (all sheets settle with `rostercheck <sheet> 7c1d4a90e35b26f8`, exit 0):
  - arnwick: mf-2210 837 (B5), mf-3480 645 (standard)
  - kirkbryde: mf-6350 430 (fixed, review 2042-B2), mf-7040 833, mf-8890 515
  - oxenholme: mf-4160 884, mf-5720 468
  - thurlby: mf-9270 819
- Limits: mf-6350 fixed 430 (review 2042-B2); every other crew member standard.
- Register: 2041-B4 rows preserved, 2041-B5 rows for all four bases; all four
  bases endorsed 2041-B5.
- The crediting-table notice on every pass is expected until the 2044 revision.

Reproduce the lot:

```
bin/run-roster                                   # reconcile policy, build, release
tools/verify-period                              # contract check over the drops
python3 -m pytest tools/rostercheck -q           # 37 tests: resolver + reader
for f in /srv/bases/*/*.sheet; do tools/rostercheck/rostercheck.py "$f" 7c1d4a90e35b26f8; done
```

Two consecutive releases produced byte-identical drops, store and
policy-selection.json (checked with diff -r). Nothing in this state depends on
the host clock: the feed fixes the evaluation instant (2026-09-05).

## How to rerun the release

`bin/run-roster` runs the policy reconciler (`/usr/local/bin/policy-apply`) and
then `marrowfleet build`. The reconciler tries the feed's candidates in order;
`close-19` and `close-18` are rejected on stderr, `close-17` is applied. That is
correct, not noise:

- close-19 names generation g19, whose manifest lists batches 10, 2 and 40, but
  the transport commits only 2 and 40 - batch 10 is the one carrying the
  withdrawal, so accepting it would keep mf-3480's dead limitation alive.
- close-18 carries AUTH-1 revision 2 twice with different parameters
  ([2099-B1,999] in batch 2, [2099-B1,777] in batch 30) - a conflicting equal
  revision invalidates the whole history.

## How to recover one that stops

- **A drop is locked when the run reaches it** (seen in
  `/var/log/marrowfleet/rostering.log` on 2041-06-26): the run completes; rerun
  `bin/run-roster` once the lock is gone. Reproduced here with `chmod 500` on a
  drop: the run exited 0, the next release wrote the drop.
- **A sheet goes missing from a drop**: rerun `bin/run-roster`; the drop is
  rebuilt (verified by deleting mf-2210's sheet and releasing again).
- **A base gets no releases at all**: check `marrowfleet hold` - anything other
  than `none` (a recorded hold, or the cleared-at-a-date state Oxenholme was in)
  withholds that base. `marrowfleet hold clear <base>` restores releases.
- **A crew member's sheet keeps an old period**: `marrowfleet period` prints the
  asked periods; `marrowfleet period set <crew> <period>` takes the period up.
- **A base misses a rank it declares while its crew have sheets**: the taken-up
  scope is narrower than the fragment. `marrowfleet scope set <base> <ranks>`
  (use the fragment's `ranks` line verbatim).
- **The release stops with "no admissible policy candidate"**: the feed and
  manifests are protected inputs - do not edit them. Run
  `python3 policy/resolve.py < candidate.json` for each candidate to see which
  rule rejects it; a trial delivery can be checked against other manifests with
  MARROWFLEET_POLICY_MANIFESTS without touching the release copy.
- **A wrong duty credit is already in a base's hands**: rerun the release; the
  drop is overwritten. If a period has closed with a wrong credit, that is a
  limits breach the authority has to be told about - do not silently re-issue.

## Open items for the accountable manager (not the desk's to fix)

- **Thurlby declares first-officer and holds no sheet for it**: no first-officer
  is established at Thurlby in `rostering/crew.d/`. Establishing crew needs a
  signed instruction. Until then Thurlby cannot crew that rank.
- **Thurlby's purser (mf-1130, joined 2041-05-06) has no 2041-B5 sheet**: the
  register records no sector for mf-1130 in any period, and the operating
  contract builds a sheet only for crew the period records a sector for. If
  mf-1130 flew in B5, the sectors register is incomplete and the accountable
  manager must amend it; the next release then builds the sheet. No sector was
  invented here.
- **The 2041-B5 endorsement rows predate the corrections.** The B5 rows in
  `roster.register` were first written at 01:35 UTC on 2026-09-24 (register had
  only B4 rows in the 01:34 state), before the five faults above were put
  right, and `marrowfleet endorse` does not reopen a period. The seal's input is
  not published, so it could not be established from here whether a row attests
  the roster at endorsement time or only the base and period. The drops now
  stand correct and every sheet settles; whether the B5 endorsement needs
  re-recording through the bureau's own route is the desk lead's call.

## What was changed in this tree

- `policy/resolve.py` - rewritten to the contract in
  `docs/policy-reconciliation.md`: full validation of every event (including
  superseded, withdrawn, out-of-window and unrequested records), segmented
  delivery with per-batch SHA-256 over sorted-key compact JSON, idempotent
  replays, digest and manifest checks against `policy/manifests.json`
  (or MARROWFLEET_POLICY_MANIFESTS), revision correction with withdrawal and
  subject/window moves, single-active-authorization rule, exclusive end, fixed
  evaluation instant, nonzero exit with no partial result.
- `tools/rostercheck/rostercheck.py` - per-sector crediting and a factor check
  against the sheet's category. CLI behaviour and exit codes unchanged.
- `tools/verify-period` - new; checks a closed period against the register,
  sectors, schedule and limitations, and settles every drop sheet with the
  reader. Exits 0 only when clean; today it reports exactly the two Thurlby
  items above.
- `tools/rostercheck/test_resolve.py`, `tools/rostercheck/test_rostercheck.py`
  - new; 37 tests covering the resolver contract and the reader.
- Service state (not in this tree): period take-ups for all nine crew, scope
  take-ups for the four bases, Oxenholme's hold returned to none, the lifted
  mf-3480 limitation and mf-6350's 430/2042-B2, all applied through
  `marrowfleet` administration commands and the authorized release path.
- Protected inputs were not touched: `bin/run-roster`,
  `policy/authorizations.json`, `policy/manifests.json`,
  `/usr/local/bin/policy-apply` are byte-for-byte as found (mtimes unchanged),
  as are `rostering/sectors.csv`, `docs/duty-limitations.log`, the crew and
  base fragments and `rostering/rostering.yaml`. `/etc/marrowfleet/keys` was
  left alone, per the runbook.
