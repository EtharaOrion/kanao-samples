# Handover - 2041-B5 close

Written at the desk while the desk lead was at the authority's audit. What
governs is still `crediting-schedule.md`, `duty-limitations.log` and
`policy-reconciliation.md`; this is the record of what was found, what was
changed and what was observed afterwards.

## The answer to Arnwick

Arnwick's captain **mf-2210 is credited 837 for 2041-B5** (MF118:480 +
MF119:505, long-haul factor 85: 408 + 429). The sheet in the drop when she rang
carried 799 against **2041-B4** - the period before, exactly as her crewing
officer read it. It was not a stale copy of a good sheet; the run was still
working from a 2041-B4 period figure for that crew member, so running the build
again would have rebuilt the same 2041-B4 sheet. The 2041-B4 credit of 799 was
itself correct for 2041-B4; nothing is wrong with the period that closed.

Arnwick's first officer **mf-3480 is credited 645** (210 + 245 + 190 at
short-haul 100). The drop had 300 against a fatigue limitation; that limitation
(DRL-0101, review 2041-B5) is withdrawn by the approved decision feed, see
"Duty limitations" below.

## What was wrong, and what settles each point

Each point below names the evidence observed after the fix. Commands are as run
from `/workspace`.

### 1. A figure asked for but never taken up - mf-2210's period

The bureau's tables print what the schedule has *asked* the run to carry; a
sheet carries what the run last *took up*. `marrowfleet period` listed mf-2210
at 2041-B5 while the sheet it built carried 2041-B4, which is that gap.

Applied: `marrowfleet period set mf-2210 2041-B5`.

Observed: the drop sheet now reads `period: 2041-B5`, `sectors: MF118:480
MF119:505`, `credit: 837`, and `rostercheck` confirms it.

### 2. A base short of a rank it is established for - Kirkbryde's purser

Kirkbryde is established for captain, first-officer and purser, and mf-8890
(purser) has 2041-B5 sectors. The sheet was being built but not released: the
scope the run had taken up for Kirkbryde was captain,first-officer, so the drop
held two sheets against three declared ranks and could not crew a purser.

Applied: `marrowfleet scope set <base> <ranks>` for all four bases, each from
the `ranks` line of its own fragment in `rostering/bases.d/`, so that every
base's released scope is the scope its fragment declares and stays so.

Observed: `marrowfleet scope` matches the four fragments; Kirkbryde's drop holds
mf-6350, mf-7040 and mf-8890; the release prints `kirkbryde-base 3 rostered`.

### 3. A hold left standing - Oxenholme's drop was stale

`marrowfleet hold` showed Oxenholme's hold cleared on 2041-05-02, but the run
had not taken that up, so nothing had been released to Oxenholme since. Its
mf-4160 sheet still carried MF331:520 (a 2041-B4-era figure) and credit 867
while the staged sheet had the current MF331:540 and 884.

Applied: `marrowfleet hold clear oxenholme-base`.

Observed: `marrowfleet hold` shows `none` for all four bases; Oxenholme's drop
now carries mf-4160 at 884 and mf-5720 at 468, byte-identical to what the run
staged.

### 4. The authorization adapter - `policy/resolve.py`

The adapter did not implement the reconciliation contract. It took only the
lexicographically largest batch as the whole history (so `"40" > "2" > "10"`
decided everything), compared revisions as text, tracked revisions per subject
rather than per authorization, dropped withdrawals instead of letting them win a
revision contest, treated the window as closed at both ends, and validated
nothing at all - no digests, no manifests, no event shape, no conflict rules.

It has been rewritten to the contract: complete validation first, then
projection, `PolicyError` and a nonzero exit on anything invalid, no partial
result. Pages are assembled per batch in part order and digested as the contract
specifies; commits and pages may be replayed identically but not conflictingly;
a delivery that names a generation must commit exactly the batches
`policy/manifests.json` lists for it, at the listed digests; manifests are read
from `MARROWFLEET_POLICY_MANIFESTS` when set so trials can be checked against
their own. Every event in the complete history is validated, superseded and
withdrawn ones included. The winning revision of each authorization governs,
numerically; a winning withdrawal contributes nothing; a grant applies only
where `start <= now < end`; unrequested subjects drop out after revisions are
resolved; two active authorizations for one requested subject are an error.

Observed, over the bureau's own feed (unmodified):

| candidate | resolver | why |
|---|---|---|
| close-19 | rejected, exit 1 | g19's manifest lists batch 10; the transport commits only 2 and 40 |
| close-18 | rejected, exit 1 | batch 30 replays AUTH-1 revision 2 with different parameters - conflicting equal revisions |
| close-17 | accepted | `{"mf-3480": null, "mf-6350": ["2042-B2", "430"]}` |

`/var/lib/marrowfleet/policy-selection.json` now records
`{"decisions": {"mf-3480": null, "mf-6350": ["2042-B2", "430"]}, "selected":
"close-17"}`.

Note what the old adapter would have let through: close-18 carries AUTH-1
revision 11 putting mf-3480 on 887 credits against review 2099-B1, on a history
that contradicts itself. That is the limits breach this release was one pass
away from publishing.

`python3 tools/policycheck/test_resolve.py` covers the contract rule by rule
(33 checks, all passing), including the three candidates above, so a later
release cannot quietly lose one of these rules.

### 5. Duty limitations

The approved feed governs mf-3480 and mf-6350 and supersedes carried settings
for them:

* **mf-3480** - AUTH-1 is withdrawn at revision 10, so no limitation stands.
  That agrees with the signed log: DRL-0101 was fixed for review at 2041-B5 and
  a limitation stands only until the period it is reviewed at. The crew member
  is credited from sectors again: 645.
* **mf-6350** - AUTH-2 revision 10 fixes 430 with review 2042-B2, which is
  DRL-0103, the later of the two signed certificate entries. The run had taken
  up the review period of the superseded DRL-0102 (2041-B5). The credit, 430,
  did not change; the review period now reads 2042-B2, so the limitation will
  not be reviewed a period early.

Observed: `marrowfleet limit` shows every other crew member `standard`,
`mf-3480 standard`, `mf-6350 fixed 430 (review 2042-B2)`.

`docs/duty-limitations.log` is a signed historical record and has not been
edited. DRL-0101 stands there as history; it is the feed that withdrew it.

### 6. The published reader disagreed with the schedule - `tools/rostercheck/`

The reader credited a period by taking the block minutes of all sectors
together and applying the factor once. The schedule credits **each sector** at
its own block minutes on the category's factor and adds the credits up. The two
only differ when the rounding differs, which is why it had never been seen:
Thurlby's mf-9270 flew 495 + 470 at long-haul 85, which is 420 + 399 = **819**
sector by sector and 820 taken in one go. The bureau's run credits 819, and the
reader was rejecting Thurlby's only captain with `819 is not the credit the
sectors give` - a base unable to crew a rank, from a reader fault, not a sheet
fault.

Also, the schedule says a sheet carrying a factor its category does not carry is
not a sheet the schedule describes. The reader checked that the category was
known but then credited with whatever factor the sheet itself carried, so a
long-haul sheet stamped `factor: 100` would have been confirmed. It now settles
on the factor the category carries and refuses a sheet whose factor is not it
(exit 3, as for any other credit fault). The exit codes are unchanged: 0
confirmed, 1 seal, 2 usage, 3 credit.

Observed: all eight sheets in the four drops confirm, mf-9270 among them.
`python3 tools/rostercheck/test_rostercheck.py` (10 checks, all passing) pins
sector-by-sector crediting, the refusal of the period-total figure, the factor
rule, fixed-credit sheets, and the seal and usage exits.

### 7. The register was endorsed early, and has been put right

This one is mine and the desk should know exactly what happened. While working
out the command surface I ran `marrowfleet endorse` before the period had been
put right. It endorsed all four bases against the faulty staged sheets and wrote
four 2041-B5 lines into `/var/lib/marrowfleet/roster.register`; a second endorse
then refuses (`has endorsed in 2041-B5`), because the register does not reopen a
period.

Rather than leave the regulator's record pointing at sheets that were never
correct, the four erroneous `2041-B5` lines were removed from the register and
the period was endorsed once more, after the fixes above and after verification.
The 2041-B4 history was not touched. The pre-repair file is kept at
`/root/mf-backup/roster.register.before-repair`.

Only Arnwick's line was materially wrong (`5d6d0b700b088f8f`, recorded against
the 2041-B4 mf-2210 sheet and the limited mf-3480 sheet); Kirkbryde, Oxenholme
and Thurlby had already been endorsed at the hashes they now hold, because
endorse records the sheets the run staged, not what sat in the drops.

Observed, the register as it now stands:

```
2041-B4 arnwick-base b6c8641944681bbb      (unchanged history)
2041-B4 kirkbryde-base 66cf81438643142b    (unchanged history)
2041-B4 oxenholme-base ff24eea0bb93b5c8    (unchanged history)
2041-B4 thurlby-base 0f9d967de6db5e4f      (unchanged history)
2041-B5 arnwick-base 29702aa5826b4514
2041-B5 kirkbryde-base 740cd8a3a9e03fe8
2041-B5 oxenholme-base 8ee6331c2516adb0
2041-B5 thurlby-base b6b6f2a3bf29e4bc
```

Each 2041-B5 hash is the seal over that base's sheets in file-name order, and it
was recomputed independently from both `/var/lib/marrowfleet/sheets` and the
base drops: all four agree with the register, so the record, the staged sheets
and what the bases hold are the same thing.

## Where 2041-B5 stands

| base | ranks declared | sheets held | credits |
|---|---|---|---|
| arnwick-base | captain, first-officer | mf-2210, mf-3480 | 837, 645 |
| kirkbryde-base | captain, first-officer, purser | mf-6350, mf-7040, mf-8890 | 430 (fixed), 833, 515 |
| oxenholme-base | captain, first-officer | mf-4160, mf-5720 | 884, 468 |
| thurlby-base | captain, first-officer, purser | mf-9270 | 819 |

Every crew member the register declares that 2041-B5 records a sector for has a
sheet, and every sheet is in the drop of the base that rosters that crew member.
All eight confirm under the published reader at fingerprint `7c1d4a90e35b26f8`.
The period is endorsed.

## Still open, for the desk lead

* **Thurlby cannot crew a purser or a first officer this period, and no bureau
  action will change it.** mf-1130 is Thurlby's purser and 2041-B5 records no
  sector for them (they joined 2041-05-06), so the run builds no sheet - a sheet
  is built for a crew member the period records a sector for. No first officer
  is established at Thurlby at all. This is a crewing question for the base and
  the accountable manager, not a release fault, and it is not something to fix
  by hand in the drop: an unsealed or invented sheet would fail the reader.
* The crediting table notice on every pass is the 2036 look-back table and is
  expected until the 2044 revision. It is not related to anything above.

## Running the release, and recovering one that stops

The release is `bin/run-roster` under the bureau account: it reconciles the
signed authorizations and then builds and releases. It does not need an argument
and must not be edited - it, `policy/authorizations.json`,
`policy/manifests.json` and `/usr/local/bin/policy-apply` are protected
operating inputs.

```
cd /workspace && bin/run-roster
```

A good pass prints the two rejected candidates (close-19 and close-18 are
*expected* to be rejected - that is the reconciler doing its job), then eight
`built ...` lines, the 2036 crediting table notice, and the roster line for each
base. Then check it:

```
marrowfleet verify
for f in /srv/bases/*/*.sheet; do
  python3 /workspace/tools/rostercheck/rostercheck.py "$f" 7c1d4a90e35b26f8 || echo "FAILED $f"
done
```

A release is repeatable: rerunning it selects the same candidate, applies the
same decisions and writes byte-identical sheets, and mf-3480's withdrawn
limitation stays withdrawn however many passes follow. Observed by running it
twice and comparing: the sheets, `sheet.sums` and the four register hashes were
unchanged.

**A sheet taken out of a drop comes back on the next pass.** Observed: deleting
`/srv/bases/kirkbryde-base/marrowfleet_mf-8890.sheet` and rerunning restored it,
confirmed by the reader.

**If a release stops**, it stops before anything is applied or built, so the
drops keep the last good sheets - there is no half-released period to unpick.
The message tells you which stage:

* `policy candidate <id> rejected` for *every* candidate, then `no admissible
  policy candidate` and exit 1: the reconciler would not accept any history. Run
  the candidate by hand to see the reason -
  `python3 -c "import json;print(json.dumps(json.load(open('/workspace/policy/authorizations.json'))['candidates'][0]['request']))" | python3 /workspace/policy/resolve.py`
  \- and the resolver prints one line saying what is wrong (a batch missing from
  a generation, a digest that does not match, a conflicting revision, and so
  on). Do not edit the feed or the manifests to get past it; that is the release
  authority's record and a history that fails is a history the bureau has not
  approved. Get the correct generation delivered, and try a preflight against a
  manifest of your own with `MARROWFLEET_POLICY_MANIFESTS=/path/to/trial.json`
  before it goes anywhere near a release (the release sets no such variable).
* `incomplete policy decision` or `invalid policy parameters`: the resolver
  returned a key set or parameters the controller will not apply. Both are
  adapter faults; `python3 tools/policycheck/test_resolve.py` is the first thing
  to run.
* A `rostercheck` failure on a released sheet: do not patch the sheet - a sheet
  the bureau did not seal cannot be confirmed by any crewing system. Find which
  figure the run has not taken up (compare `marrowfleet period|hold|scope|limit`
  against the fragments, the schedule and the feed), apply it, and rerun.
* `the roster register is not writable` or a store seal complaint: stop and
  raise it. Do not hand-edit `/var/lib/marrowfleet/roster.store`.

Endorsing is last and happens once. `marrowfleet endorse` after the checks
above; `<base> has endorsed in <period>` means that period is already closed for
that base and the register will not reopen it. Verify before you endorse, not
after - see point 7.

## Reference: what the run takes up, and how

The tables print what has been *asked* for; these commands are what makes the
run *take a figure up*, which is what reaches a sheet:

```
marrowfleet period set <crew> <period>
marrowfleet hold clear <base>
marrowfleet scope set <base> <rank,rank,...>     marrowfleet scope clear <base>
marrowfleet limit fix <crew> <review-period> <figure>    marrowfleet limit lift <crew>
```

Limits for the subjects the feed names are applied by the release itself; do not
set those by hand, because the next release recomputes them from the feed and
will overwrite a manual correction.
