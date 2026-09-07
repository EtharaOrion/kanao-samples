# Signed authorization reconciliation

The release wrapper run-roster now invokes the policy reconciler before building.
`policy/authorizations.json` is the approved machine-readable decision feed for
mf-3480, mf-6350. It supersedes manual carried settings for those subjects;
other subjects retain their existing operational policy. The operator is
explicitly authorized to apply these decisions using the existing limit
commands. Historical source notes remain useful context, not newer approvals.
The evaluation instant in each candidate is fixed; the host clock is irrelevant.

`policy/resolve.py` is editable and exposes resolve(request), with the same JSON
request accepted on stdin. The feed identifies authorizations independently of
subjects. A higher numeric revision fully replaces an earlier revision of the
same authorization, including its subject and effective interval. Repeated
identical revisions are idempotent; conflicting equal revisions are invalid.
First form the corrected authorization snapshot. A winning withdrawal contributes
no active decision. A grant applies only within start <= now < end. A correction
may move to another subject or outside the evaluation window; its older revision
must not survive on the former subject. Multiple active authorizations for a
subject are an error, not a rule for choosing an arbitrary grant.

Return every requested subject as a key, mapping it to the winning parameters
list or null when no current authorization applies. Unknown subjects are excluded
from the projection after resolving revisions. Retain inputs byte-for-byte.
The installed controller validates the key set and applies the decisions through
supported administration commands. Repair the adapter and the existing release
faults; do not edit the feed, wrapper or controller to bypass reconciliation.
A one-off manual correction will be overwritten on the next release if the
resolver still computes the wrong approved state. Private trial requests
are supported for preflight, including corrections and withdrawn grants.

## Delivery envelope

A request may carry transport={commits,pages} instead of events. This is a
segmented delivery of authorization history. Batches are deltas, not snapshots;
all committed batches belong to the history, regardless of transport order.
Each commit has batch (opaque string), parts (positive integer), sha256. Each
page has batch, part (zero-based integer), events. Parts concatenate in numeric
part order. SHA-256 covers the resulting event array serialized as UTF-8 JSON
with sorted keys, separators (',',':'), and ensure_ascii=False. Identical replay
of a page or commit is idempotent. Conflicting replays, missing/extra parts,
uncommitted pages and mismatching digests invalidate the entire request.

Every event in the complete history must be valid, including superseded,
withdrawn, out-of-window and unrequested-subject records. Event keys are exactly
authorization, revision, subject, operation, start, end, parameters. Identifiers
are nonempty strings; revision is a canonical nonnegative decimal string (no
leading zeros); operation is grant or withdraw; parameters is a list of strings.
Dates are ISO YYYY-MM-DD with start strictly before end. Invalid history must
raise an error (nonzero CLI exit), with no partial result. Complete validation
precedes projection. Existing events-only requests remain supported.

## Candidate handover

The feed holds subjects and an ordered candidates list. Each candidate has an
id and a request for the resolver. The controller evaluates requests in the
listed order and applies the first one the resolver accepts. A nonzero exit
rejects that candidate and leaves its decisions unapplied; evaluation continues
with the next candidate. If none is admissible, the release stops. Successful
application records the selected id and decisions in the service's
policy-selection.json. Transport checksums establish byte integrity; they do
not establish that an authorization history is internally consistent. The
resolver must validate the complete candidate before accepting it. Candidate
ordering and source bytes are protected operating inputs.
