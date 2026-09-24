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

## Corrections, 2041-B5 (see docs/handover-2041-B5.md)

Two things in the notes above turned out to be wrong when tested against the
tooling:

* "The Arnwick drop was locked ... and it cleared on its own the same minute."
  It does not clear on its own. A hold recorded against a base stops every
  write to that base's drop until `marrowfleet hold clear <base>` is run, and
  the hold display is misleading - Oxenholme showed `cleared 2041-05-02` and
  still refused writes. If a drop will not update, clear the hold and rerun the
  release.
* "Run the build again and the drop catches up." A rebuild does not move a
  sheet: sheets keep the figures they last took up. Bring the figure up
  (`marrowfleet period set <crew> <period>`, and the reconciler's
  `limit fix`/`lift`), then rerun the release.

`marrowfleet verify` only says that every base holds at least one sheet; it
said nothing about a wrong credit, a sheet from the period before, or a missing
purser. `python3 /workspace/tools/settle/settle.py <period>` is the check that
catches those, and it is what found the 2041-B5 faults.
