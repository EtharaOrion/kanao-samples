# Bureau notes

Notes the rostering desk keeps for each other. Not a procedure and not approved
by anyone; the crediting schedule and the duty limitations are what govern.

## Nights

The run goes out from `bin/run-roster` under the bureau account at 03:40. It
prints the crediting table notice every time, which is expected until the 2044
revision.

Two nights ago the Arnwick drop was locked when the run reached it and the write
retried; the line is in `/var/log/marrowfleet/rostering.log` and it cleared on
its own the same minute.

The base drops are served off the bureau's own volume, so a sheet that is taken
away comes back on the next pass.

## Periods past

2041-B4, Kirkbryde rang about a sheet showing a credit from the period before.
What put it right that time was running the build again - the drop caught up on
the second pass and the crewing officer saw the new figure the same afternoon.
Whoever picks up a call like that first should try that before anything else.

2040-B6, we spent a morning on an Oxenholme query that turned out to be a duty
limitation nobody had told the crewing officer about. The credit was being fixed
quite correctly and the bureau had nothing to put right.

## Keys

`/etc/marrowfleet/keys` was closed to the bureau group when key custody came in
house and the mode on that tree is in the operator's security schedule. It was
widened once, in 2038, and the finding took five months to close. Leave it.

## 2041-B5 close, 2041-06-24 (desk notes for whoever picks this up)

Four things were wrong at once when Arnwick rang about a credit "from the
period before", and only one of them was the thing she rang about.

**What was actually wrong, and the evidence for each**

1. *Arnwick's stale period figure (the call).* mf-2210's taken-up period
   figure still stood at 2041-B4, so the run kept releasing a 2041-B4 sheet
   (credit 799) into the Arnwick drop while the sectors register stood at
   2041-B5. `marrowfleet period` printed 2041-B5 for everyone because the
   table prints what has been *asked for*; the run builds from what it has
   *taken up*. Evidence: the released sheet carried `period: 2041-B4`;
   `notice period mf-2210 2041-B5` then `marrowfleet period set mf-2210
   2041-B5` took the figure up and the next build produced `period: 2041-B5`,
   credit 837 (sectors MF118:480 MF119:505 at factor 85).

2. *Kirkbryde's purser sheet never released.* mf-8890 was built and staged
   (`built mf-8890`, `kirkbryde-base 3 rostered`) but never reached the drop,
   so Kirkbryde held a drop short of the purser rank it declares - a base in
   that state cannot crew a purser flight. The taken-up scope for
   kirkbryde-base did not include purser. Evidence: with the drops emptied,
   `build` released only mf-6350 and mf-7040; after `notice scope
   kirkbryde-base captain,first-officer,purser` and `marrowfleet scope set
   kirkbryde-base captain,first-officer,purser`, the same build released all
   three. Thurlby's scope was taken up the same way for good order.

3. *Oxenholme's release was held.* A hold stood against oxenholme-base
   (`hold` showed `cleared 2041-05-02`), and a base with a hold records no
   release at all - its drop kept whatever it already had, including a sheet
   that no longer matched the staged one. Evidence: `marrowfleet hold clear
   oxenholme-base`, then the drop rewrote on the next pass. If a base's drop
   ever looks frozen, read `marrowfleet hold` first.

4. *mf-9270's credit and the published reader.* The build wrote 819 for
   mf-9270 (sectors MF640:495 MF641:470, long-haul, factor 85) and
   `rostercheck` refused it, demanding 820. The two floor in different
   places: the run credits each sector at the factor and adds the credits
   (495×85/100 = 420.75 → 420; 470×85/100 = 399.75 → 399; 819), while
   rostercheck floored the summed block minutes (965×85/100 = 820). The
   schedule's own words ("a sector is credited at the block minutes it was
   flown for, taken on the crediting factor … and the duty credit on the
   sheet is the credits of that period's sectors added together") settle
   each sector first and add, so the reader was the one deviating;
   `tools/rostercheck/rostercheck.py` `credited()` now floors per sector and
   sums. Every other sheet this period agreed under both readings (837, 645,
   884, 468, 833, 515 and the fixed 430), so no settled figure moved. The
   builder cannot be changed from this repository, so the reader carries the
   correction; **flag it to the authority** that a crewing system holding
   the pre-correction reader would still read 820 against this sheet - that
   is a republish, not a limits breach, but it wants telling.

5. *Policy reconciliation had been selecting a bad candidate.* The adapter in
   `policy/resolve.py` read only the pages of the highest-numbered batch,
   treated the manifest as optional and compared revisions as strings, so
   candidate close-18 (a superseded generation) resolved to phantom 887/888
   credits for mf-3480/mf-6350. It now reassembles every committed batch,
   checks each digest and the generation's manifest, validates every event,
   folds revisions per authorization (a higher revision fully replaces an
   earlier one, including its subject and interval; equal revisions must be
   identical), suppresses withdrawn authorizations, applies `start <= now <
   end`, and rejects a subject with two active authorizations. Evidence:
   close-19 rejects (generation g19 does not commit manifest batch 10),
   close-18 rejects (conflicting equal revisions of AUTH-1), close-17
   resolves to `mf-3480: null, mf-6350: ["2042-B2","430"]`; that is what
   `/var/lib/marrowfleet/policy-selection.json` records and what the
   release applied: mf-3480 lifted to standard (645), mf-6350 fixed at 430
   with review moved to 2042-B2.

**Where the period stands now**

All four bases are endorsed for 2041-B5 and the register records it (2041-B4
lines are history and were left alone). Every sheet in every drop settles
under `tools/rostercheck/rostercheck.py <sheet> 7c1d4a90e35b26f8` with exit 0:
arnwick mf-2210 837, mf-3480 645; kirkbryde mf-6350 430 (fixed, review
2042-B2), mf-7040 833, mf-8890 515; oxenholme mf-4160 884, mf-5720 468;
thurlby mf-9270 819.

**What is still open, and is not ours to fix**

Thurlby declares captain, first-officer and purser and holds only a captain.
No first-officer is established there at all, and its only purser (mf-1130,
joined 2041-05-06) has no sectors recorded in 2041-B5, so the register
correctly builds no sheet - a sheet is built only for crew the period records
sectors for. Thurlby cannot crew a first-officer or purser flight from this
period's drop. Establishing crew is the accountable manager's against a signed
instruction, not the desk's, so this is a phone call, not a change.

**How to rerun the release**

```
cd /workspace && bin/run-roster
```

That reconciles policy (first candidate the resolver accepts) and builds and
releases every sheet. Reconciliation runs on every release: a second run from
the same feed and manifests selects close-17 again, applies the same
decisions, and rebuilds byte-identical sheets - verified by hashing every
staged and released sheet across consecutive runs. A limitation the selected
history withdraws stays lifted however many releases follow.

**How to recover one that stops**

- A base's drop frozen or holding a sheet that no longer matches the staged
  one: read `marrowfleet hold` - a hold stops that base's release entirely;
  `marrowfleet hold clear <base>` then rerun `bin/run-roster`.
- A sheet missing from a drop, or an old-period sheet sitting in one (the
  2041-B4 Kirkbryde call, and Arnwick's this time): rerun `bin/run-roster`.
  The drops are served off the bureau's volume and the run rewrites each
  drop's sheets every pass; both faults above were healed by a rerun once the
  underlying figure had been taken up.
- A sheet still not reaching a drop although `build` reports the base
  rostered: the run has not taken up the figure. Compare what the tables show
  (the ask) with what the run holds, then take it up - `marrowfleet period
  set <crew> <period>`, `marrowfleet scope set <base> <ranks>`, `marrowfleet
  hold clear <base>`, `marrowfleet limit fix <crew> <review> <credit>` /
  `marrowfleet limit lift <crew>` - and rerun the release. `notice <table>
  <key> <figure>` only records the ask; the table will show it either way.
- `policy-apply` rejecting every candidate: the resolver prints its reason on
  stderr for each candidate it refuses. Run a candidate by hand to see it -
  `python3 - <<'EOF'` feeding `resolve.py` the candidate's request on stdin -
  or check a trial delivery against manifests of its own with
  `MARROWFLEET_POLICY_MANIFESTS=/path/to/trial.json`. Do not edit the feed,
  the manifests or the wrapper; they are protected operating inputs and the
  controller sets no manifests variable.
- Never reopen an endorsed period. The register does not reopen one; a
  correction goes in the next period.

