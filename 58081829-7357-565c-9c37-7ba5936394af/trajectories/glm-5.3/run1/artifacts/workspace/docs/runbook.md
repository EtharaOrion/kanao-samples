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
away comes back on the next pass. [checked 2041-B5: it does not. The run only
writes a sheet whose checksum has changed since it last released it, and it
strips sheets it holds no release record for, so a sheet that goes missing from
a drop stays missing however many passes follow. Run `tools/drop-audit --fix`
after the pass; it restores the drop from the staged sheets. Full detail in
`docs/handover-2041-B5.md`.]

## Periods past

2041-B4, Kirkbryde rang about a sheet showing a credit from the period before.
What put it right that time was running the build again - the drop caught up on
the second pass and the crewing officer saw the new figure the same afternoon.
Whoever picks up a call like that first should try that before anything else.

2040-B6, we spent a morning on an Oxenholme query that turned out to be a duty
limitation nobody had told the crewing officer about. The credit was being fixed
quite correctly and the bureau had nothing to put right.

## 2041-B5 close

Arnwick rang about a credit from the period before; there were three separate
faults behind the calls we were getting (a stale period figure never taken up,
a policy candidate the reconciler should have rejected, and a sheet the run
strips from Kirkbryde's drop on every pass). What was observed and what was
done is written up in `docs/handover-2041-B5.md`, with the rerun and recovery
steps. Read that before touching another period.

## Keys

`/etc/marrowfleet/keys` was closed to the bureau group when key custody came in
house and the mode on that tree is in the operator's security schedule. It was
widened once, in 2038, and the finding took five months to close. Leave it.
