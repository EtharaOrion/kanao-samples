# Handover - the 2041-B5 bidding period

Written for whoever holds the rostering desk next, and for the desk lead on her
return from the authority's audit. Everything below was observed on the bureau
host; each point carries the command that shows it.

## The Arnwick call

Arnwick's crewing officer was right. Her captain's sheet in the drop carried the
period before.

- Observed: `/srv/bases/arnwick-base/marrowfleet_mf-2210.sheet` read
  `period: 2041-B4`, `sectors: MF110:460 MF111:480`, `credit: 799` - the
  2041-B4 sectors - while `marrowfleet period` showed mf-2210 at 2041-B5.
- What the bureau has credited mf-2210 with for 2041-B5: **837**, from
  `MF118:480 MF119:505` at the long-haul factor of 85 (480x85/100 = 408,
  505x85/100 = 429).
- Now: `python3 tools/rostercheck/rostercheck.py /srv/bases/arnwick-base/marrowfleet_mf-2210.sheet 7c1d4a90e35b26f8`
  prints `arnwick-base mf-2210 2041-B5: 837 credited`.

Rerunning the build on its own would never have fixed this, whatever the note in
`runbook.md` says about 2041-B4 at Kirkbryde. The run builds from the figure it
last took up; the 2041-B5 figure had been asked for and never applied.

## What was wrong, and what was done about it

Five faults. Each was fixed at its cause, so the fix survives the next release.

### 1. Figures asked for but never applied (period, scope, hold)

`marrowfleet period|hold|scope|limit` print what the schedule has asked the run
to carry. The run builds from what it last took up. Three figures had been
noticed and never applied, so three tables and the drops disagreed:

| Table | Asked | Taken up | Effect |
|-------|-------|----------|--------|
| period, mf-2210 | 2041-B5 | 2041-B4 | Arnwick's stale captain sheet |
| scope, kirkbryde-base | captain,first-officer,purser | captain,first-officer | purser sheet built but never released |
| hold, oxenholme-base | cleared 2041-05-02 | still held | nothing released to Oxenholme at all |

- Kirkbryde, observed: `marrowfleet build` reported `kirkbryde-base 3 rostered`
  while `/srv/bases/kirkbryde-base/` held two sheets; mf-8890's sheet stood in
  `/var/lib/marrowfleet/sheets/` and had never reached the base. A base short of
  a rank it declares cannot crew it - Kirkbryde could not have crewed a purser.
- Oxenholme, observed: its drop did not move on a build (mtimes stayed behind),
  a sheet deleted from it did **not** come back on the next pass, and
  `marrowfleet_mf-4160.sheet` carried `MF331:520` with `credit: 867` when the
  sectors register records `MF331` at 540 block minutes. The note in
  `runbook.md` that "a sheet that is taken away comes back on the next pass" is
  not true of a base whose hold has not been taken up.

Applied, once, with the bureau's own administration commands:

```
marrowfleet period set mf-2210 2041-B5
marrowfleet scope set kirkbryde-base captain,first-officer,purser
marrowfleet hold clear oxenholme-base
```

Now: all four tables agree with every sheet in every drop; Kirkbryde holds three
sheets (captain, first-officer, purser); Oxenholme holds `mf-4160` at **884**
(500x85/100 = 425, 540x85/100 = 459) and `mf-5720` at 468; `marrowfleet hold`
reads `none` for every base.

### 2. The reconciler was selecting an invalid authorization history

`bin/run-roster` runs `/usr/local/bin/policy-apply`, which walks the candidates
in `policy/authorizations.json` in order and applies the first one
`policy/resolve.py` accepts. The old adapter accepted anything: it read only the
batch whose name sorted highest, compared revisions as text ("9" > "10"),
ignored withdrawals, and checked no digest, no manifest and no event.

- Observed effect: candidate `close-19` was accepted although generation `g19`
  lists batch `10` in `policy/manifests.json` and the transport never commits
  it - the batch carrying the withdrawal of AUTH-1. mf-3480's limitation was
  reinstated, and its sheet read `limit: fixed`, `credit: 300`, against a limit
  table that read `standard`.

`policy/resolve.py` was rewritten to the validation contract in
`docs/policy-reconciliation.md`: complete validation before any projection,
transport parts concatenated in part order and digested (sorted keys,
`(',',':')`, `ensure_ascii=False`), a named generation matched exactly against
the manifests, every event in the whole history checked (including superseded,
withdrawn, out-of-window and unrequested-subject records), revisions ordered
numerically with a higher revision fully replacing an earlier one - subject and
window included - equal revisions idempotent only when identical, a winning
withdrawal contributing nothing, and more than one active authorization for a
subject treated as an error.

Now, from `bin/run-roster`:

```
policy candidate close-19 rejected      # generation g19 does not commit batch 10
policy candidate close-18 rejected      # AUTH-1 carries conflicting revision 2
```

and `/var/lib/marrowfleet/policy-selection.json` records

```
{"decisions": {"mf-3480": null, "mf-6350": ["2042-B2", "430"]}, "selected": "close-17"}
```

which is what `docs/duty-limitations.log` says in its own words: DRL-0101
against mf-3480 was for review at 2041-B5 and so does not stand in this period,
and DRL-0103 carries mf-6350 on at 430 for review at 2042-B2. mf-3480 is
credited **645** for 2041-B5 (210 + 245 + 190 at the short-haul factor of 100);
mf-6350 stays fixed at 430, and `marrowfleet limit` now shows its review at
2042-B2.

The feed, the manifests, the wrapper and the controller were not touched; the
adapter was the fault and the adapter was repaired, so the next release
recomputes the same approved state rather than inheriting a manual correction.

### 3. The published reader did not settle a credit the way the schedule does

- Observed: `rostercheck` refused thurlby-base's captain -
  `rostercheck: 819 is not the credit the sectors give` - on a sheet the bureau
  had sealed. The reader added the block minutes first and then took the factor
  (965x85/100 = 820); the schedule credits each sector on the factor and adds
  the sector credits together (495x85/100 = 420, 470x85/100 = 399, so 819), and
  that is what the run builds. mf-9270 was the only crew member in the period
  where the two arithmetics differ, which is why this had not been seen.
- `tools/rostercheck/rostercheck.py` now credits per sector, and also refuses a
  sheet whose `factor` is not the factor its `category` carries - the schedule
  says such a sheet is not one it describes, and the old reader would have taken
  the sheet's own factor on trust.

Bases hold their own copy of this reader. **Their copies need replacing with
this one**; until they do, a crewing officer at Thurlby will see that refusal on
a sound sheet. That is the one item of this handover that leaves the bureau.

### 4. Nothing was endorsed for the period

The roster register carried only 2041-B4. After the checks below, all four bases
were endorsed once for 2041-B5; `/var/lib/marrowfleet/roster.register` now
carries both periods and the 2041-B4 rows are untouched. A second `marrowfleet
endorse` answers `has endorsed in 2041-B5` and changes nothing - the register
does not reopen a period.

### 5. Open, and not the run's to fix: thurlby-base

Thurlby is established for captain, first-officer and purser and holds one
sheet.

- No first-officer is declared at thurlby-base anywhere in `rostering/crew.d/`
  (`marrowfleet plan`).
- mf-1130, the thurlby purser, records no sector in 2041-B5 (or in 2041-B4):
  `grep 1130 rostering/sectors.csv` returns nothing. A sheet is built for a crew
  member the period records a sector for, so no sheet is correct here.

So Thurlby cannot crew first-officer or purser this period. Neither can be put
right from the rostering desk: establishing a crew member is the accountable
manager's act against a signed instruction, and the flown sectors are a signed
record - adding a crew fragment or a sectors row would have made the register
say something that did not happen. **This needs the accountable manager (for the
first-officer establishment) and the crewing desk (for mf-1130's allocation),
and the authority will read the endorsement as it stands.** Raised here rather
than papered over.

## What each base is holding at the close

Every sheet below confirms under the published reader and the bureau's key
fingerprint `7c1d4a90e35b26f8`.

| Base | Crew | Rank | Period | Credit |
|------|------|------|--------|--------|
| arnwick-base | mf-2210 | captain | 2041-B5 | 837 |
| arnwick-base | mf-3480 | first-officer | 2041-B5 | 645 |
| kirkbryde-base | mf-6350 | captain | 2041-B5 | 430 (fixed, DRL-0103) |
| kirkbryde-base | mf-7040 | first-officer | 2041-B5 | 833 |
| kirkbryde-base | mf-8890 | purser | 2041-B5 | 515 |
| oxenholme-base | mf-4160 | captain | 2041-B5 | 884 |
| oxenholme-base | mf-5720 | first-officer | 2041-B5 | 468 |
| thurlby-base | mf-9270 | captain | 2041-B5 | 819 |

Arnwick, Kirkbryde and Oxenholme hold a sheet for every rank they are
established for. Thurlby does not, for the reason in point 5.

## Checks anyone can repeat

```
# every sheet in every drop, read the way a crewing officer reads it
for f in /srv/bases/*/*.sheet; do
  python3 /workspace/tools/rostercheck/rostercheck.py "$f" 7c1d4a90e35b26f8 || echo "REFUSED $f"
done

marrowfleet verify        # the bureau has a roster for every base
marrowfleet status        # the period the sectors register is current at
marrowfleet period ; marrowfleet hold ; marrowfleet scope ; marrowfleet limit

python3 /workspace/tools/policycheck/test_resolve.py      # 49 checks, the reconciliation contract
python3 /workspace/tools/rostercheck/test_rostercheck.py  # 12 checks, the crediting schedule
```

Observed when this period was closed: all eight sheets confirmed, `verify`
clean, both suites green, and the tables agreeing with the sheets.

## Running the release, and recovering one that stops

Rerun, at any time, from the bureau account:

```
/workspace/bin/run-roster
```

It reconciles first and builds second. It is safe to run again: a second release
from the same feed and manifests selects the same candidate, applies the same
decisions and writes the same sheets. Observed here - three consecutive releases
left every sheet byte-identical (`sha256sum`), the same
`policy-selection.json`, and the register untouched.

If a release stops, it stops before it changes anything. Work down this list.

1. **`policy candidate ... rejected` for every candidate, then
   `ValueError: no admissible policy candidate`, exit 1.** Nothing was built and
   no limit was applied - observed by running the release against a manifest
   file naming no generation: the sheets, the selection file and the limit table
   were all unchanged afterwards. Read the rejection line for each candidate; it
   names the rule the history broke. Try the candidate by hand without touching
   anything:

   ```
   python3 - <<'EOF'
   import json, subprocess
   feed = json.load(open('/workspace/policy/authorizations.json'))
   for c in feed['candidates']:
       r = subprocess.run(['python3', '/workspace/policy/resolve.py'],
                          input=json.dumps(c['request']), text=True, capture_output=True)
       print(c['id'], r.returncode, r.stdout.strip() or r.stderr.strip())
   EOF
   ```

   A trial delivery of your own can be checked against manifests of its own with
   `MARROWFLEET_POLICY_MANIFESTS=/path/to/manifests.json`; the release itself
   sets no such variable. If every candidate is genuinely inadmissible the
   release is meant to stop - that is a decision for the release authority, not
   a thing to edit around. The feed, `policy/manifests.json`, `bin/run-roster`
   and `/usr/local/bin/policy-apply` are protected inputs.

2. **A sheet is missing from a drop, or a drop is stale.** Rerun the release;
   the drops are written from the staged sheets every pass. Observed: a sheet
   deleted from the Kirkbryde drop came back byte-identical on the next release.
   If it does not come back, the base is being held back or is short in its
   scope - check `marrowfleet hold` and `marrowfleet scope` against
   `rostering/bases.d/`, and take the figure up with `marrowfleet hold clear
   <base>` or `marrowfleet scope set <base> <ranks>`.

3. **A sheet carries the wrong period or a stale credit.** Compare the tables
   with the sheet. A figure that stands in a table but not on a sheet has been
   asked for and not applied; apply it (`marrowfleet period set <crew>
   <period>`), then rerun the release. Do not expect a plain rebuild to move it.

4. **`rostercheck` refuses a sheet.** Exit 1 is the seal - the sheet was altered
   after the bureau sealed it, or it was read against the wrong fingerprint;
   rerun the release to replace it. Exit 3 is the credit or the factor - check
   the sectors register and `docs/duty-limitations.log` before anything else,
   and make sure the base is running this repository's reader.

5. **The run refuses the store** (`refusing to run against a store it did not
   write`, `the roster store does not carry this run's seal`). Stop; that is the
   service state, not something to rebuild around. Raise it with the desk lead.

Endorsing is the last thing, after the checks above are clean:

```
marrowfleet endorse
```

Once for a base in a period. It will refuse a second time, and the register does
not reopen a period.

## Not touched

`bin/run-roster`, `policy/authorizations.json`, `policy/manifests.json`,
`/usr/local/bin/policy-apply`, `rostering/sectors.csv`, the crew and base
fragments, `docs/duty-limitations.log`, the historical 2041-B4 rows in the
roster register, and `/etc/marrowfleet/keys` - which stays as it is.
