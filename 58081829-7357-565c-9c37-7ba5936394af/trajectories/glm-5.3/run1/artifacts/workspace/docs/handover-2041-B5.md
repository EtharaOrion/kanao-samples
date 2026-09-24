# Handover - the 2041-B5 close

Written for whoever picks up the rostering desk next. Everything below was
observed on this machine during the 2041-B5 close (desk lead at the Gatwick
audit). The register, the signed feed, the manifests, the release wrapper and
the controller are untouched; what changed is listed under "What I changed".

The judgement the desk is measured on: what a base is **holding** when the
period closes. In that currency, at close:

```
arnwick-base    mf-2210 2041-B5 credit 837   standard
arnwick-base    mf-3480 2041-B5 credit 645   standard
kirkbryde-base  mf-6350 2041-B5 credit 430   fixed (review 2042-B2)
kirkbryde-base  mf-7040 2041-B5 credit 833   standard
kirkbryde-base  mf-8890 2041-B5 credit 515   standard
oxenholme-base  mf-4160 2041-B5 credit 884   standard
oxenholme-base  mf-5720 2041-B5 credit 468   standard
thurlby-base    mf-9270 2041-B5 credit 819   standard
```

All eight settle under `tools/rostercheck/rostercheck.py` with fingerprint
`7c1d4a90e35b26f8` (exit 0), the register is endorsed for 2041-B5 at all four
bases, and `marrowfleet verify` passes.

## 1. What Arnwick rang about, and what was actually wrong

The sheet in Arnwick's drop for mf-2210 carried `period: 2041-B4`,
`sectors: MF110:460 MF111:480`, credit 799 - a credit from the period before,
which is the call the crewing officer made. Three faults were found; only the
first explains her call.

### (a) mf-2210's period figure had been asked for and never taken up

Observed, before anything was changed:

* `marrowfleet period` printed `mf-2210 2041-B5` (the figure the bureau asked
  the run to carry), while the staged sheet at
  `/var/lib/marrowfleet/sheets/marrowfleet_mf-2210.sheet` said
  `period: 2041-B4` (what the run had last taken up).
* Rebuilding alone did not close the gap: `bin/run-roster` printed
  `built mf-2210 (213 bytes)` every pass and left the B4 content standing. The
  README's "the two agree for as long as nobody asks for a change without
  applying it" is exactly what had happened - asked, never applied.
* Recovery was `marrowfleet period set mf-2210 2041-B5` (the apply form;
  it prints `period mf-2210 -> 2041-B5` and writes the store), then
  `marrowfleet build`. The staged sheet moved to `period: 2041-B5`,
  `sectors: MF118:480 MF119:505`, credit 837, and the release into the drop
  followed on the next pass because the sheet's checksum had changed.

The 2041-B4 runbook note ("run the build again and the drop catches up") is
about a *release* that had not landed. This was a different fault - a figure
that had not been *taken up* - and rebuilding does not fix it. Note added to
`docs/runbook.md`.

### (b) The reconciler was applying a candidate it must reject (wrong credit)

This is the "credit that is wrong is a limits breach" item. Before the close,
the drop for **mf-3480** at Arnwick carried `limit: fixed`, `credit: 300` -
the DRL-0101 fatigue figure whose review period is 2041-B5, i.e. it does not
stand this period. A wrong fixed credit of 300 against 645 minutes of credited
duty is the limits breach the authority would have to be told about. It went
out in the release before this one; the sheet from that release is preserved
in the pre-close backup (see "Audit trail").

Root cause: `policy/resolve.py` ignored the delivery contract in
`docs/policy-reconciliation.md` - no manifest check, no page/commit digest
check, revisions compared as strings, only the highest-numbered batch
projected, `now <= end` instead of `now < end`. Fed the three candidates in
`policy/authorizations.json` it accepted `close-19`, whose generation g19 does
not commit batch 10 that the g19 manifest lists, and whose only authority for
fixing mf-3480 at 300 is AUTH-1 revision 9 - superseded history.

What the corrected resolver does with the feed, candidate by candidate:

* `close-19` (g19): **rejected** - the g19 manifest lists batches 10, 2 and 40;
  the transport commits only 2 and 40. "A batch the manifest lists that the
  transport does not commit ... invalidates the entire request."
* `close-18` (g18): **rejected** - AUTH-1 revision 2 appears twice with
  different parameters (999 in batch 2, 777 in batch 30). "Conflicting equal
  revisions are invalid."
* `close-17` (g17): **accepted** - commits exactly batches 10 and 2 at the
  manifest's digests; every page matches its commit digest; AUTH-1's winning
  revision is 10, a withdraw, so mf-3480 has no current authorization;
  AUTH-2's winning revision is 10, a grant of `2042-B2`/`430`, in window.
  Result: `{"mf-3480": null, "mf-6350": ["2042-B2", "430"]}`.

That agrees with the signed record the bureau already holds:
`docs/duty-limitations.log` reviews DRL-0101 (mf-3480, 300) at 2041-B5 - the
current period, so it no longer stands - and carries DRL-0103 (mf-6350, 430)
forward to review at 2042-B2. The controller therefore lifted mf-3480's limit
and fixed mf-6350 at 430 with review 2042-B2; `marrowfleet limit` now shows
exactly that, and mf-3480's sheet re-credited to 645 standard (210+245+190 at
factor 100). Repeated releases from the same feed select close-17 again
(`/var/lib/marrowfleet/policy-selection.json` records it), so the withdrawal
stays applied however many releases follow.

`policy/resolve.py` is rewritten to implement the whole contract (transport
envelope, manifests, complete validation before projection, numeric revision
resolution per authorization, withdrawal wins, window `start <= now < end`,
multiple active authorizations for a subject as an error, projection of the
requested subjects, nonzero exit with no partial result on any fault). It reads
manifests from `MARROWFLEET_POLICY_MANIFESTS` when set, else
`/workspace/policy/manifests.json`. Contract tests: `python3
policy/test_resolve.py` - 28 cases, all passing, including the three live
candidates (close-19 and close-18 must be rejected).

### (c) The published reader did not settle the run's arithmetic

`tools/rostercheck/rostercheck.py` credited `sum(blocks) * factor // 100`,
while the schedule ("a sector is credited at the block minutes it was flown
for, taken on the crediting factor ... the duty credit on the sheet is the
credits of that period's sectors added together") and the run both credit each
sector and then add. The two disagree only where truncation bites, which this
period is mf-9270: per-sector 420+399 = **819** (on the sheet) against
whole-sum **820** (the old reader), so Thurlby's crewing system was rejecting
a correct sheet (rostercheck exit 3). The reader now credits per sector, and
also settles the factor from the crew member's category rather than trusting
the sheet's own factor line ("a crew member is credited at the factor of the
category their fragment carries, and no other"). All eight sheets settle.

### (d) The run strips mf-8890 from Kirkbryde's drop on every pass

`marrowfleet build` reports `kirkbryde-base 3 rostered` and stages
mf-8890's sheet, but the sheet is **absent from `/srv/bases/kirkbryde-base`
after every pass** - present before the pass, gone after it, whether or not
its entry in `/var/lib/marrowfleet/sheet.sums` is present. No other sheet
behaves this way. The run has never released that sheet (the pre-close drop
did not have it either), and its endorsement seal for kirkbryde-base in
`/var/lib/marrowfleet/roster.register` covers **all three** of the base's
sheets, so the run's own register says the drop should hold it. This is a
fault in the run binary; it cannot be patched here and it will recur.

Work-around in place: `tools/drop-audit` (below) checks every drop against the
staged sheets, the reader and the register after each pass, and `--fix`
restores any missing sheet byte-identical from `/var/lib/marrowfleet/sheets`.
Run it after every nightly pass until the vendor fixes the run. It was run at
the close; Kirkbryde holds all three sheets and its drop again matches the
2041-B5 endorsement seal.

## 2. How to rerun the release

```
cd /workspace
python3 policy/test_resolve.py      # resolver contract tests, must all pass
bin/run-roster                     # policy-apply (reconciles the feed) + build
python3 tools/drop-audit --fix     # restore anything the pass stripped
python3 tools/drop-audit           # must finish: every declared base holds
                                   # this period's sheets, settled
marrowfleet verify
marrowfleet limit                  # mf-6350 fixed 430 (review 2042-B2),
                                   # everyone else standard
marrowfleet endorse                # only once per period per base
```

`bin/run-roster` is idempotent for a settled period: the same candidate is
selected, the same sheets are built, and only checksum changes are released.
Expect the 2036 crediting table notice on every pass (until the 2044 revision)
and, while the release fault stands, expect `drop-audit` to restore mf-8890
each time.

`marrowfleet endorse` records the period against each base in
`/var/lib/marrowfleet/roster.register` and will not reopen it; endorsing again
prints `has endorsed in 2041-B5` and changes nothing. 2041-B5 is endorsed at
all four bases.

## 3. Recovering a release that stops

A release stops before any sheet goes out if `/usr/local/bin/policy-apply`
finds no admissible candidate (`no admissible policy candidate`), which is the
correct behaviour for a feed whose every candidate is invalid - do not work
around it by hand-fixing limits, the next release overwrites them.

* **A candidate was rejected and you want to know why**: feed the candidate's
  request to the resolver on its own and read the error -
  `python3 -c "import json;print(json.dumps(json.load(open('/workspace/policy/authorizations.json'))['candidates'][0]['request']))" | python3 policy/resolve.py`
  (index 0 = close-19, 1 = close-18, 2 = close-17). A nonzero exit with a
  one-line reason is a rejection; exit 0 prints the decisions.
* **A trial delivery to check before it is in a feed**: the resolver accepts a
  request on stdin; a transport that names no generation is checked without a
  manifest, or against manifests of its own via `MARROWFLEET_POLICY_MANIFESTS`.
* **A sheet is missing from a drop after a pass**: `python3
  tools/drop-audit --fix`. The run does not re-release unchanged content, so
  this is the only route; the file it copies is the run's own staged sheet,
  seal and all.
* **A sheet shows the wrong period or a figure the tables disagree with**:
  check `marrowfleet period` against the sheet's `period:` line. If the table
  is ahead of the sheet, the figure was asked for and never applied:
  `marrowfleet period set <crew> <period>`, then `marrowfleet build`, then
  `drop-audit --fix`.
* **A wrong fixed credit has gone out**: re-run the release with the corrected
  resolver (the decision is recorded in
  `/var/lib/marrowfleet/policy-selection.json`), confirm the new sheet settles,
  and tell the authority - see the mf-3480 note in 1(b).
* **State to fall back to**: the pre-close state of `/var/lib/marrowfleet`,
  `/srv/bases` and `/var/log/marrowfleet` is preserved in `/root/backup/`
  (`state-*.tgz`, taken before any change, plus `roster.store.orig`,
  `resolve.py.orig`, `bases-pre-endorse/`).

## 4. Open items for the accountable manager (not settled, and why)

* **Thurlby declares `captain,first-officer,purser` and holds one sheet.** No
  first-officer is established at Thurlby in `rostering/crew.d/` (the only
  first-officers are mf-3480 at Arnwick, mf-5720 at Oxenholme and mf-7040 at
  Kirkbryde - each the only one at their base, so a move only moves the gap),
  and Thurlby's purser, mf-1130, has no sectors recorded in 2041-B5, so under
  the operating contract ("a duty sheet is built for every crew member the
  register declares that the period records a sector for") no purser sheet is
  due. Thurlby therefore cannot crew a first-officer or purser flight this
  period. Crew are established and stood down only by the accountable manager
  against a signed instruction, so this is escalated rather than fixed:
  establish a first-officer at Thurlby before 2041-B6 if those flights are to
  go. `tools/drop-audit` reports the gap on every run until it is closed.
* **The release fault in 1(d)** needs a vendor fix; until then the post-pass
  `drop-audit --fix` carries it.
* **mf-1130** joined 2041-05-06 and flew nothing in the period, so no sheet is
  due; if Kirkbryde-style calls come in about a "missing" sheet, check the
  sectors register first.

## 5. What I changed (and what I did not)

Changed:

* `policy/resolve.py` - rewritten to the reconciliation contract (1(b)).
* `policy/test_resolve.py` - new, contract tests for the resolver.
* `tools/rostercheck/rostercheck.py` - per-sector crediting and the factor
  settled from the category (1(c)).
* `tools/drop-audit` - new, post-pass drop audit and restore (1(d), 2, 3).
* `docs/runbook.md` - corrected the false "comes back on the next pass" note,
  added the 2041-B5 entry.
* `docs/handover-2041-B5.md` - this file.
* Register finalised: 2041-B5 endorsed at all four bases; the applied figures
  the run now holds are `mf-6350 fixed 430 (review 2042-B2)` and standard
  limits elsewhere; `marrowfleet period set mf-2210 2041-B5` applied.

Not changed, by instruction: `bin/run-roster`, `policy/authorizations.json`,
`policy/manifests.json`, `/usr/local/bin/policy-apply` (verified byte-for-byte
against their pre-close SHA-256 after the close), the signed duty limitations,
`rostering/sectors.csv`, the crew and base fragments, the keys tree at
`/etc/marrowfleet/keys` (mode is in the operator's security schedule - leave
it), and the historical 2041-B4 register lines.
