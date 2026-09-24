# Handover: the 2041-B5 bidding period

Written for whoever picks up the desk next, and for the desk lead on return from
the authority's audit. Every figure below was read off the live service after
the final release; the commands that produce it are at the end.

## The Arnwick call

Arnwick's crewing officer was right. The sheet in their drop credited
mf-2210 at 799 against **2041-B4** while the sectors register is current at
2041-B5. The bureau now credits mf-2210 with **837** for 2041-B5:

```
sectors: MF118:480 MF119:505   long-haul, factor 85
         480*85/100 = 408      505*85/100 = 429      credit 837
```

The sheet standing in `/srv/bases/arnwick-base` carries that figure and settles
under `tools/rostercheck`. The other Arnwick sheet moved too: mf-3480 was
carrying a fixed credit of 300 that no current authorization supports, and is
now credited 645 from its own sectors (see below).

## What was wrong, and what settles it

Each point lists what was observed, what was done, and what was seen afterwards.

### 1. The release applied a withdrawn limitation to mf-3480

Observed: `policy/resolve.py` took only the highest-named batch of a segmented
delivery, checked no digest, no manifest, no event and no revision, and
compared revisions as strings. It accepted candidate `close-19` and applied
`limit fix mf-3480 2041-B5 300` from AUTH-1 revision 9 - a record superseded by
AUTH-1 revision 10, which withdraws the authorization.

Done: `policy/resolve.py` rewritten to the contract in
`docs/policy-reconciliation.md`. It validates the complete candidate before it
projects anything: page/part reassembly and SHA-256 per batch, the generation's
manifest, every event's schema, revision conflicts anywhere in the history,
half-open grant windows, and one current authorization per subject.

Observed after (`tools/policycheck/test_resolve.py`, and each candidate fed to
the resolver by hand):

```
close-19  rejected  generation g19 lists batch 10, which the delivery does not commit
close-18  rejected  AUTH-1 revision 2 arrived with conflicting records
close-17  accepted  {"mf-3480": null, "mf-6350": ["2042-B2", "430"]}
```

That agrees with the signed limitations log: DRL-0103 carries mf-6350 at 430 to
review 2042-B2, and nothing current stands against mf-3480.
`/var/lib/marrowfleet/policy-selection.json` records `close-17` and those
decisions; `marrowfleet limit` now holds `mf-6350 fixed 430 (review 2042-B2)`
and mf-3480 standard. Rerunning the release selects the same candidate and
builds the same sheets.

### 2. The run had not taken up this period for mf-2210

Observed: the sheet was built at 2041-B4 while `marrowfleet period` asked
2041-B5 for every crew member - a figure asked for and never applied.

Done: `marrowfleet period set <crew> 2041-B5` for all nine crew members, so what
the run holds is what the register asks. Observed after: every sheet carries
`period: 2041-B5` and the sectors the register records for that period.

### 3. Kirkbryde was holding no purser sheet

Observed: the build staged mf-8890 (purser) and reported "kirkbryde-base 3
rostered", but only two sheets reached the drop. The scope the run had taken up
for kirkbryde did not carry `purser`, which its fragment declares.

Done: `marrowfleet scope set <base> <ranks>` for all four bases, from the ranks
each base fragment declares. Observed after: `/srv/bases/kirkbryde-base` holds
three sheets, one for each rank the base is established for.

### 4. Nothing had been released into Oxenholme's drop

Observed: a hold stood against oxenholme-base (`marrowfleet hold` printed
`cleared 2041-05-02`, a note of a clearance the run had never taken up), and no
file in that drop had been written by any recent pass. The mf-4160 sheet
standing there credited 867 over `MF331:520` - neither the 540 block minutes the
period records nor the 884 the bureau had staged.

Done: `marrowfleet hold clear oxenholme-base`, then the release. Observed after:
both Oxenholme sheets are byte-identical to the staged ones, mf-4160 credits
884, and the stale sheet is gone.

### 5. The published reader disagreed with the crediting schedule

Observed: `tools/rostercheck` pooled the block minutes and then applied the
factor. The schedule credits each sector on the factor and adds the sector
credits together; the two differ whenever a sector's credit has a fraction. The
reader refused mf-9270's sheet - `819 is not the credit the sectors give` - and
would have accepted 820, which no sector of that period gives:

```
495*85/100 = 420    470*85/100 = 399    credit 819
```

The reader also settled a sheet against whatever `factor` the sheet itself
carried, so a sheet carrying a factor its category is not credited at would have
passed - the schedule says such a sheet is not one it describes.

Done: `credited()` now credits per sector, and the reader takes the factor from
the crew member's category and refuses a sheet that states any other.
`tools/rostercheck/test_rostercheck.py` holds both cases. Observed after: all
eight sheets in the drops settle, exit 0.

## Where the period stands

Endorsed, after verification, once per base:

```
2041-B5 arnwick-base   29702aa5826b4514
2041-B5 kirkbryde-base 740cd8a3a9e03fe8
2041-B5 oxenholme-base 8ee6331c2516adb0
2041-B5 thurlby-base   b6b6f2a3bf29e4bc
```

The 2041-B4 entries are untouched and the period is not reopened; a second
`marrowfleet endorse` in the same period prints `has endorsed in 2041-B5` and
writes nothing.

What each base is holding at the close of the period:

```
base             sheet      rank            limit            credit
arnwick-base     mf-2210    captain         standard            837
                 mf-3480    first-officer   standard            645
kirkbryde-base   mf-6350    captain         fixed  DRL-0103     430
                 mf-7040    first-officer   standard            833
                 mf-8890    purser          standard            515
oxenholme-base   mf-4160    captain         standard            884
                 mf-5720    first-officer   standard            468
thurlby-base     mf-9270    captain         standard            819
```

Every one of those files is byte-identical to the sheet the bureau staged in
`/var/lib/marrowfleet/sheets`, and every one settles under the published reader
with the register's fingerprint `7c1d4a90e35b26f8`.

## Still open - for the accountable manager, not the desk

`tools/periodcheck` reports these as *cover* findings (exit 2). They cannot be
settled by whoever runs the period, because crew members are established and
bases opened only against a signed instruction, and the flown sectors are a
signed input:

* **thurlby-base is established for `first-officer` and the register declares no
  first-officer at that base at all.** The base cannot crew that rank from its
  drop, this period or any other, until either a first-officer is established
  there or the base's established ranks are changed.
* **thurlby-base is established for `purser`, and its purser mf-1130 (joined
  2041-05-06) flew no sector in 2041-B5**, so the period records nothing to
  credit and no sheet is built. Either the sectors return for mf-1130 is
  outstanding or the base has no purser cover this period; the crewing officer
  at Thurlby should be told which before a flight is planned on it.

Nothing was invented to close these: a sheet with no sectors behind it would be
a credit the schedule does not describe.

## Running a release, and recovering one that stops

Release, from `/workspace`:

```
./bin/run-roster          # reconciles the signed feed, builds, releases the drops
```

The "2036 crediting table" notice on every pass is expected until the 2044
revision; it is not a fault.

Check before endorsing - none of these write anything:

```
python3 tools/policycheck/test_resolve.py     # the reconciliation contract
python3 tools/rostercheck/test_rostercheck.py # the crediting schedule
python3 tools/periodcheck/periodcheck.py      # the period against the register
```

`periodcheck` exits 0 when nothing is found, 1 on a fault the bureau must put
right, 2 when only cover findings stand. It settles every staged sheet and every
sheet in every drop against the register, the period's sectors, the crediting
schedule, the signed limitations, the signed feed and the published reader, and
it names anything standing in a drop that does not belong there.

Then, and only then:

```
marrowfleet endorse
```

Recovering a run that stops or a drop that goes wrong:

* **A sheet is missing from a drop, or a drop holds a sheet the bureau did not
  stage.** Rerun `./bin/run-roster` and rerun `periodcheck`. Verified here by
  deleting kirkbryde's mf-8890 sheet and editing the credit on arnwick's
  mf-2210: `periodcheck` reported both (`was not released into the
  kirkbryde-base drop`, `not the one the bureau staged`, and the reader refusing
  the seal), and one rerun restored both to the staged bytes.
* **The release stops with `no admissible policy candidate`.** Every candidate
  was rejected. Feed each one to the resolver to see the rule it broke:
  `python3 -c "import json,subprocess,sys; feed=json.load(open('policy/authorizations.json')); [print(c['id'], subprocess.run([sys.executable,'policy/resolve.py'],input=json.dumps(c['request']),capture_output=True,text=True)) for c in feed['candidates']]"`.
  The message names the rule (a manifest batch, a digest, an event, a revision
  conflict). Do not edit the feed, the manifests, the wrapper or the controller
  to get a candidate through - a manual `marrowfleet limit` correction is
  overwritten by the next release anyway.
* **A sheet carries the wrong period, a base is missing a rank, or a drop is not
  written at all.** The tables print what has been asked for; the sheets show
  what the run last took up. Apply the figure and rerun the release:
  `marrowfleet period set <crew> <period>`, `marrowfleet scope set <base>
  <ranks>`, `marrowfleet hold clear <base>`.
* **`marrowfleet verify` is not evidence.** It reports only that a roster exists
  for each base; it said so while Kirkbryde was short a purser sheet and
  Oxenholme's drop was three weeks stale. Use `periodcheck`.

## What was touched

Changed: `policy/resolve.py`, `tools/rostercheck/rostercheck.py`; added
`tools/periodcheck/periodcheck.py`, `tools/policycheck/test_resolve.py`,
`tools/rostercheck/test_rostercheck.py`, this note.

Unchanged, byte for byte: `bin/run-roster`, `policy/authorizations.json`,
`policy/manifests.json`, `/usr/local/bin/policy-apply`, the register and its
fragments, `rostering/sectors.csv`, `docs/crediting-schedule.md`,
`docs/duty-limitations.log`, and the mode on `/etc/marrowfleet/keys`.

One thing to declare: while working out the command forms, `marrowfleet endorse`
was run before the sheets were right, and it wrote four 2041-B5 lines into
`/var/lib/marrowfleet/roster.register`. The file was restored byte-for-byte from
the copy taken before that command, and the period was endorsed once, after
verification, from the sheets described above. The 2041-B4 record was never
altered. `endorse` takes no confirmation and no dry run - keep it for last.
