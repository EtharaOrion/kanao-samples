# Marrowfleet Rostering - the crew roster run

What the rostering bureau publishes to the bases it holds crew for at the close
of each bidding period. The run itself is the `marrowfleet` command; this
repository holds the register it expands, the period's flown sectors and the
bureau's own notes.

## How a period goes out

One fragment per crew member lives in `rostering/crew.d/` and one per base in
`rostering/bases.d/`, and the run expands whatever it finds - a crew member or a
base exists for the run exactly when its fragment does. A duty sheet is built
for every crew member the register declares that the period records a sector
for. A duty sheet goes out at one bidding period across every crew member the
register declares, and the sectors register is current at one bidding period at
a time. `bin/run-roster` builds the period and releases the base drops; nothing
reaches a base until it has.

The run writes each sheet into the drop that carries its base's name. A base
crews from the ranks it is established for and no others, so the run releases
into each base's own drop the sheet of every crew member that base rosters, and
a base holding a drop short of the ranks it declares cannot crew a flight from
it. The ranks a base is established for are declared in its own fragment.

A base's crewing system does not take the bureau's word for a sheet. The crewing
system settles a sheet with the reader the bureau publishes, and the reader
settles a duty credit the way the schedule settles it; `tools/rostercheck/` is
that reader as the bureau published it, and crewing officers run it over what
stands in their own drop before a crew member is rostered onto a flight. A crew
member is credited the duty its sectors and its category's crediting factor give
it, unless a duty limitation fixes the credit, and a limitation stands until the
period it is reviewed at. `docs/crediting-schedule.md` carries the schedule and
`docs/duty-limitations.log` carries what the bureau has fixed.

Endorsing is the last thing the bureau does. A base is endorsed once in a
bidding period, the roster register records what it took, and the register does
not reopen a period. That is the record the regulator reads when it asks what a
base was rostered against a given period.

## The bureau's tables

`marrowfleet period`, `hold`, `scope` and `limit` print the bureau's tables -
the figures the schedule has asked the run to carry. A figure appears there from
the moment it is asked for. What a sheet carries is a different thing: the run
works from what it last took up, and it takes a figure up when that figure is
applied to it. The two agree for as long as nobody asks for a change without
applying it.

`marrowfleet verify` walks the sheets the run staged. `marrowfleet status`
prints the bidding period the sectors register is current at.

## Layout

```
rostering/rostering.yaml   the register: sheets root, base root, key, base
rostering/sectors.csv      the flown sectors, by crew member and period
rostering/crew.d/          one fragment per crew member
rostering/bases.d/         one fragment per base
bin/run-roster             build the period and release the base drops
tools/rostercheck/         the reader the bases' crewing systems settle with
docs/                      the schedule, the limitations and the bureau's notes
```

Crew members are established and stood down, and bases opened and closed, by the
bureau's accountable manager against a signed instruction, never by whoever is
running the period. The signing keys under `/etc/marrowfleet/keys` were closed
when the bureau took key custody in house and the mode on that tree is in the
operator's security schedule.
