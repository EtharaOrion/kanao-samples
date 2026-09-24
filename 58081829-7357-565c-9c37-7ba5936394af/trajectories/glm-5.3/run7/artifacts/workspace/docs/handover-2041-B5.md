# Handover - 2041-B5 duty sheets, close of period

Desk: rostering bureau. Desk lead at the authority's audit in Gatwick until
Thursday. Written by whoever picked the period up, for whoever picks the next
one up. The crediting schedule and the duty limitations still govern; this is
the record of what was found, what was done, and how to run it again.

## The call and what was actually wrong

Arnwick rang at 08:20: their drop carried a duty credit belonging to the
period before, on the captain's sheet. Three faults were found; only the
first is what Arnwick saw.

1. **mf-2210's sheet was built at 2041-B4 in a 2041-B5 release.** The run
   builds from the figures it last took up, and those live in
   `/var/lib/marrowfleet/roster.store`, not in the register. For this one
   crew member the store still held the 2041-B4 take-up, so every nightly
   `build` re-emitted the old sheet (sectors `MF110:460 MF111:480`, credit
   799) while the other seven sheets went out at 2041-B5. The Arnwick
   drop-lock in the log (`2041-06-26T03:40:07Z ... retried`, cleared the
   same minute) was not the cause and the runbook's 2041-B4 advice - run the
   build again - does not touch this class of fault: rebuilding re-emits
   what the store holds. Removing `roster.store` so the run takes up the
   current figures fresh is what put it right (see Recovery).
2. **The release was applying an invalid policy candidate.** `policy-apply`
   had been selecting `close-19`, whose transport commits batches 2 and 40
   while generation g19's manifest (`policy/manifests.json`) also lists
   batch 10. The resolver in `policy/resolve.py` ignored everything outside
   the highest-numbered batch, compared revisions as strings ("2" > "12"),
   never checked the manifest, the page envelope or the event contract, and
   applied whatever the last page carried. With it, mf-3480 was held at a
   fixed 300 that the selected history withdraws - a limits breach the
   authority would have to be told about. See the next section for what the
   corrected resolver does and how it was checked.
3. **The published reader rejected a correct sheet.** `rostercheck` settled
   a credit by truncating the *sum* of block minutes against the factor, so
   mf-9270's 2041-B5 sheet (495 and 470 minutes at 85) came out as "should
   be 820" against the run's 819. The schedule credits each sector at its
   own block minutes taken on the factor and then adds the sector credits:
   420 + 399 = 819. A base whose crewing system cannot settle a sheet is a
   base holding no usable sheet, so the reader was repaired to the
   schedule's arithmetic, and to refuse a sheet whose factor is not its
   category's factor (the schedule: a sheet carrying a factor the category
   does not carry is not a sheet the schedule describes).

Nothing in `rostering/` was touched: the register, the fragments, the flown
sectors and the signed duty limitations are as they came in, and
`bin/run-roster`, `policy/authorizations.json`, `policy/manifests.json` and
`/usr/local/bin/policy-apply` are byte-for-byte unchanged (original mtimes
intact, 2026-09-23 00:59:57).

## What the release now applies, and why

`policy/resolve.py` now does what `docs/policy-reconciliation.md` says:
validates the whole delivery envelope (commits, parts, page digests,
idempotent vs conflicting replays, uncommitted pages, parts outside their
commit), checks a generation's committed batches against the manifest's
exact batch set and digests, validates every event in the complete history
including superseded, withdrawn, out-of-window and unrequested-subject
records, and only then resolves revisions numerically per authorization.
Equal revisions must be identical; conflicting equal revisions invalidate
the whole request. A winning withdrawal leaves nothing active; a grant
applies only within `start <= now < end`; multiple active authorizations on
one subject are an error, not a choice.

Against the feed in the door:

| candidate | verdict | why |
|-----------|---------|-----|
| close-19  | rejected | names generation g19 but commits `{2,40}`; the g19 manifest lists `{10,2,40}` |
| close-18  | rejected | AUTH-1 revision 2 appears twice with different parameters (`2099-B1,999` in batch 2, `2099-B1,777` in batch 30) - conflicting equal revisions |
| close-17  | applied  | commits exactly the g17 manifest `{10,2}`; AUTH-1's winning revision 10 is a withdrawal, AUTH-2's winning revision 10 grants `[2042-B2, 430]` |

So the release lifts mf-3480's limitation (credit 645 at standard, not the
carried 300) and holds mf-6350 at 430 with review 2042-B2 - which is what
DRL-0103 carried on. A limitation the selected history withdraws stays
absent however many releases follow; two releases from the same feed have
been run back to back and produce byte-identical sheets and the same
`policy-selection.json` (`close-17`).

`tools/policycheck/preflight.py` exercises the resolver over trial
requests (events-only and transport, corrections, withdrawals, every
invalidity rule above, manifest rules with its own manifests) and prints
which feed candidates are admissible. Run it before believing any change to
the resolver:

    python3 tools/policycheck/preflight.py     # 32/32, admissible: close-17

Private trial requests are supported as before; a trial can be checked
against manifests of its own by setting `MARROWFLEET_POLICY_MANIFESTS`.

## What each base is holding at the close

All sheets at 2041-B5, all settling with the published reader against the
fingerprint in the register (`7c1d4a90e35b26f8`):

| base | sheet | rank | category | factor | credit | limit |
|------|-------|------|----------|--------|--------|-------|
| arnwick-base | mf-2210 | captain | long-haul | 85 | 837 | standard |
| arnwick-base | mf-3480 | first-officer | short-haul | 100 | 645 | standard |
| kirkbryde-base | mf-6350 | captain | short-haul | 100 | 430 | fixed (review 2042-B2) |
| kirkbryde-base | mf-7040 | first-officer | long-haul | 85 | 833 | standard |
| kirkbryde-base | mf-8890 | purser | short-haul | 100 | 515 | standard |
| oxenholme-base | mf-4160 | captain | long-haul | 85 | 884 | standard |
| oxenholme-base | mf-5720 | first-officer | standby | 60 | 468 | standard |
| thurlby-base | mf-9270 | captain | long-haul | 85 | 819 | standard |

mf-2210's 837 is the period's own sectors (MF118 480, MF119 505 at 85:
408 + 429). mf-3480's 645 is the period's sectors at 100 with no limitation;
the crewing officer at Arnwick should be told the 300 is gone. mf-9270's 819
is per-sector crediting; see fault 3 above.

**Rank coverage, and the one thing the desk cannot put right:**
arnwick, kirkbryde and oxenholme hold every rank they declare. Thurlby
declares `captain,first-officer,purser` and holds only its captain:

- **purser** - mf-1130 is Thurlby's purser, established and enabled, but the
  2041-B5 sectors register records no sector for them, and a duty sheet is
  built only for a crew member the period records a sector for. If mf-1130
  flew or stood by this period, the register is short the rows and the
  accountable manager must supply them; the desk does not write flown
  sectors into the register. Until then Thurlby cannot crew a purser flight.
- **first-officer** - no first-officer is established at Thurlby at all.
  That needs a signed instruction from the accountable manager (or a
  correction to the base's declared ranks); neither is the desk's to make.

Both are escalated, not papered over: no crew member, fragment or sector
row was invented to make the coverage table look complete.

## The roster register

`/var/lib/marrowfleet/roster.register` records what each base took. The
2041-B5 lines first written over this period recorded the faulty release
(mf-2210's sheet at 2041-B4, mf-3480 held at 300), so after the corrected
release was verified and settled the four 2041-B5 lines were taken out and
the period re-endorsed: `marrowfleet endorse` rewrote them against the
corrected drops (arnwick's seal moved `5d6d0b700b088f8f` ->
`29702aa5826b4514`; the bases whose sheets did not change kept their seals).
The 2041-B4 history is byte-identical to what was found. Further endorses
are no-ops ("has endorsed in 2041-B5") - the register does not reopen a
period. The pre-repair register copy is in the attic.

## Rerunning the release

    bash /workspace/bin/run-roster

That is the whole release: the wrapper runs `/usr/local/bin/policy-apply`
(rejects candidates in order, applies the first admissible one, records
`/var/lib/marrowfleet/policy-selection.json`) and then `marrowfleet build`,
which stages the sheets under `/var/lib/marrowfleet/sheets` and releases
each into its base's drop under `/srv/bases/<base>/`. Expect on every pass:
the two `policy candidate ... rejected` lines (close-19, close-18), the
crediting-table notice about the retained 2036 table (expected until the
2044 revision - it is a notice, not a fault), and one `N rostered` line per
base (2/3/2/1). The run is idempotent: repeat releases rebuild identical
sheets, re-select `close-17`, and leave the register alone.

To verify a close, as the bases' crewing systems do:

    for s in /srv/bases/*/*.sheet; do
        tools/rostercheck/rostercheck.py "$s" 7c1d4a90e35b26f8
    done

All eight settle. `marrowfleet verify` walks the staged sheets and reports
a roster for every declared base (presence, not rank coverage - the rank
table above is the desk's own check against each base fragment's `ranks`).

## Recovering a release that stops

- **A sheet carrying the period before** (the Arnwick class): `build`
  re-emits what the store last took up, so rebuilds do not clear it. Stop,
  then reset the run's take-up and rebuild:
  1. `cp -a /var/lib/marrowfleet /var/lib/marrowfleet.attic-$(date +%F)` -
     the store is an opaque, sealed cache; keep what was there.
  2. `rm /var/lib/marrowfleet/roster.store`
  3. `bash /workspace/bin/run-roster` - the run writes a fresh store,
     takes up the current figures and rebuilds every sheet at the current
     period.
  4. Re-record the hold table it drops (`marrowfleet hold <base> <figure>`
     asks, `marrowfleet notice hold <base> <figure>` applies - as found,
     oxenholme-base was `cleared 2041-05-02`), then re-endorse if the
     period's register lines recorded the faulty sheets (take out that
     period's four lines, `marrowfleet endorse`).
  Limits come back on the next release - `policy-apply` re-applies the
  feed's decisions every time - and `scope` is read from the base
  fragments, so neither is lost to a store reset. This is what was done
  here; the fresh store has been running the releases above.
- **A release that stops at policy** (`no admissible policy candidate`):
  every candidate was rejected. Run the preflight tool above, read the
  rejections, and get the feed's owner to put the candidate right. The
  resolver must keep rejecting an inconsistent history even when its
  decisions look plausible - close-19's decisions looked right for mf-6350
  and were still invalid as a delivery. Do not widen the resolver, the
  feed, the manifests or the controller to make a release pass.
- **A drop that will not settle**: run the reader line above; exit 1 is the
  seal, exit 3 is the credit or the factor. A sheet that will not settle is
  a missing sheet for that base - rebuild before the period closes.
- **A locked drop**: the log shows the write retries and clears on its own
  (`2041-06-26`); the drops are served off the bureau's volume, so a sheet
  taken away comes back on the next pass.

## Evidence

- `/var/lib/marrowfleet/attic/2041-B5-pre-repair/` - the staged sheets,
  `sheet.sums`, `roster.register` and the last pre-reset `roster.store` as
  they stood before the repair. mf-2210's copy there shows the 2041-B4
  sheet the complaint was about. The pre-reset store also carries a few of
  this desk's own probe notices (`notice period ...` while learning the
  take-up model); none of them reached a sheet.
- `/var/log/marrowfleet/rostering.log` - the four nightly runs to
  2041-06-27, each `8 sheets, 4 bases`, nothing but the crediting-table
  notice, and the Arnwick lock that cleared itself. That is the "went
  through" the night runner reported.
- `/var/lib/marrowfleet/policy-selection.json` - `close-17`, decisions
  `{"mf-3480": null, "mf-6350": ["2042-B2", "430"]}`.
- `/etc/marrowfleet/keys` was not touched; the mode on that tree stands as
  the operator's security schedule has it.
