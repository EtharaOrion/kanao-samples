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

2041-B5, Arnwick rang about the same thing as Kirkbryde did in 2041-B4, and
running the build again did **not** put it right: the run had never taken up the
new period for that captain. Three of that release's faults were figures asked
for and never applied - a period, a base's ranks and a hold clearance - so the
tables read right while the drops did not. Two more were in our own code. Read
`handover-2041-B5.md` before working another call like it, and run
`tools/periodcheck/` over the drops: `marrowfleet verify` passed through the lot
of it, because all it looks at is whether the register holds a row per base.

Also: don't pipe the run into `head`. It dies on the closed pipe partway through
and releases nothing.

## Keys

`/etc/marrowfleet/keys` was closed to the bureau group when key custody came in
house and the mode on that tree is in the operator's security schedule. It was
widened once, in 2038, and the finding took five months to close. Leave it.
