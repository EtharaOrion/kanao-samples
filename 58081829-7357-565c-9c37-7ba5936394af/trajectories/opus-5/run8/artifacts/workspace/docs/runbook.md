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

## Corrections to these notes (2041-B5)

Two things written above are wrong and cost us a morning on the Arnwick call.
Full write-up in `handover-2041-B5.md`.

"What put it right that time was running the build again" - not in general. A
figure that has only been *asked for* shows up in `marrowfleet period` and
`marrowfleet scope` straight away, but the run builds from what it last *took
up*, so rerunning the build changes nothing until the figure is applied with
`marrowfleet period set` or `marrowfleet scope set`. Reading the table and
rerunning the build is exactly how a stale sheet survives a rebuild.

"a sheet that is taken away comes back on the next pass" - it does not. The run
only writes into a drop when the staged sheet's checksum has changed since the
last pass, so deleting a drop sheet leaves the base with nothing. Deleting
`sheet.sums` does not help either. To force a drop to be released in full:
`marrowfleet hold clear <base>` and then rerun `bin/run-roster`. That is what
the `cleared 2041-05-02` against Oxenholme in `marrowfleet hold` is.

Also: `marrowfleet verify` only checks that the bureau *staged* a roster per
base - it never opens a drop, so it passes with a stale sheet, a missing rank or
an altered sheet sitting at a base. Run `tools/closecheck/` after every release;
that is what looks at what the bases are actually holding.

And `marrowfleet endorse` has no dry run. Any argument you pass is ignored and
the period is endorsed there and then, and the register does not reopen.

## Keys

`/etc/marrowfleet/keys` was closed to the bureau group when key custody came in
house and the mode on that tree is in the operator's security schedule. It was
widened once, in 2038, and the finding took five months to close. Leave it.
