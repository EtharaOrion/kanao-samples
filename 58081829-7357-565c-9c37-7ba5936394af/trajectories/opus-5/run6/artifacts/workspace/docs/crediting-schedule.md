# Duty crediting schedule

How a bidding period's duty credit is arrived at, published by the rostering
bureau and held at the bases it rosters for. This is what a base's crewing
system settles a duty sheet against.

A sector is credited at the block minutes it was flown for, taken on the
crediting factor of the duty category the crew member is rostered in, and the
duty credit on the sheet is the credits of that period's sectors added together.
Block minutes are whole minutes off blocks.

The crediting factor is held in hundredths against the category, so a factor of
100 leaves the block minutes as they stand:

```
long-haul    85
short-haul  100
standby      60
```

A crew member is credited at the factor of the category their fragment carries,
and no other. A sheet carrying a factor the crew member's category does not
carry is not a sheet this schedule describes, whatever the credit written on it
comes to.

Where a duty limitation stands against a crew member, the figure written against
it in `duty-limitations.log` is the duty credit for the period and the sectors
do not come into it.

The 2036 crediting table is retained for look-back on periods up to and
including 2038-B6. The run prints a notice about it on every pass and will until
the 2044 revision drops it.
