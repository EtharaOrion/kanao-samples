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

2041-B5, Arnwick rang about the same symptom and running the build again did
nothing for it - the run was building from a period it had taken up and never
moved on, the signed feed was being read wrongly, Kirkbryde was short a purser
sheet and nothing had reached Oxenholme's drop for weeks. `marrowfleet verify`
was happy throughout. `docs/handover-2041-B5.md` has what each of those was and
what settles it; run `tools/periodcheck` before believing a period is out.

## Keys

`/etc/marrowfleet/keys` was closed to the bureau group when key custody came in
house and the mode on that tree is in the operator's security schedule. It was
widened once, in 2038, and the finding took five months to close. Leave it.
