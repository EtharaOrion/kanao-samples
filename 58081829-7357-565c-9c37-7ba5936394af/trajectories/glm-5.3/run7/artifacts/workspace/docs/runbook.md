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

2041-B5 (September): Arnwick's captain carried 2041-B4 into a 2041-B5 release
and rebuilding did NOT clear it. The run builds from the figures it last took
up, which live in /var/lib/marrowfleet/roster.store; for that one crew member
the store still held the old period, so every nightly build re-emitted the old
sheet. What put it right was resetting the take-up (copy the tree aside, remove
roster.store, run bin/run-roster again) - see docs/handover-2041-B5.md for the
full recovery and for what the release now applies. If a rebuild does not clear
a period-before credit, do not keep rebuilding; go to the store.

2040-B6, we spent a morning on an Oxenholme query that turned out to be a duty
limitation nobody had told the crewing officer about. The credit was being fixed
quite correctly and the bureau had nothing to put right.

## Keys

`/etc/marrowfleet/keys` was closed to the bureau group when key custody came in
house and the mode on that tree is in the operator's security schedule. It was
widened once, in 2038, and the finding took five months to close. Leave it.
