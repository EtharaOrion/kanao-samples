## Defect

A hotel reservation platform runs eight services, each in its own container on
port 8080 and each answering `/health`, `/metrics`, `/casebook`,
`/traffic-contract` and `POST /probe`. The agent works from a `main`
workstation that carries only the diagnosis document; every service runs the
same bytecode with its own name, reads a sealed scenario file, and signs every
response with an HMAC-SHA256 key the workstation does not hold. A background
workload probes every service with each of the four `reservation_class` and
`probe_mode` combinations in turn. After an admission-control change the
dashboards improved, CPU fell and health stayed green, while guests waited
longer, completions kept falling and larger bookings suffered most. The estate
bundles 1 planted fault(s) and 2 required clearance(s):

1. (focal) `reservation-worker` is `capacity-cliff`. Its effective worker
   slots for group reservations fall by one per casebook interval, from 10
   down to a floor of 2, while queue age, queue depth and scheduled-to-
   completion latency rise and completions fall. The closed-loop synthetic
   probe sends less work while the worker stalls, so the send-to-response p99
   it reports improves at the same time.
2. (clearance) `availability-cache` is healthy. Its CPU rate declines with
   completed productive work and its full CPU pressure stays at 0.002; the
   lower CPU is a consequence of less work, not spare capacity.
3. (clearance) `admission-gate` is healthy. It applies the published bounded
   admission policy, rejecting 7 more group requests per interval, and
   preserves the scheduled, admitted, rejected and completed identities.

The estate is flat: there is no dependency chain, and the causal work is a
comparison of measurement populations and lifecycle boundaries rather than a
walk of a call graph. `synthetic-probe`, `reservation-worker` and
`admission-gate` each publish the same group-reservation casebook on their own
uptime axis; the other five services publish an empty casebook that owns no
records. The reference repair is authored in shell over an embedded Python
solver (`solution/solve.sh`): it waits for every service, samples `/metrics`
and `/casebook` five times at 15 s spacing from the four evidence sources,
writes the single verdict line, and writes the evidence with the two required
population calculations and both clearances. Out of bounds: restarting,
reconfiguring or patching any service or the workload, more than two
concurrent probes against one service, and altering, shortening or
reconstructing any signed response used as evidence.

## Load-bearing work

The deliverable lands in 2 file(s):

- `/app/verdict.txt` - exactly one sorted line, `reservation-worker:capacity-cliff`
- `/app/monitor_evidence.json` - one `anomalies` entry for that pair carrying
  at least three signed `reservation-worker` observations spanning 45 s of
  its uptime, the casebook intervals the causal check reads, a `calculations`
  object with `selected_fraction_first` and `selected_fraction_latest`, and a
  reason; plus `ruled_out` entries for `availability-cache` and
  `admission-gate` with class `none`, a reason of at least 20 characters, and
  at least two signed observations from that service spanning 22.5 s

What the estate publishes, per service:

| service | port | role | casebook |
|---|---|---|---|
| `synthetic-probe` | 8080 | observer of the group population | intervals, dashboard summary, coverage manifest, single-room control |
| `reservation-edge` | 8080 | healthy | empty |
| `admission-gate` | 8080 | healthy, required clearance | same casebook as the probe |
| `reservation-worker` | 8080 | faulty, `capacity-cliff` | same casebook as the probe |
| `availability-cache` | 8080 | healthy, required clearance | empty |
| `inventory` | 8080 | healthy | empty |
| `confirmation` | 8080 | healthy | empty |
| `user-wait-log` | 8080 | healthy | empty |

The casebook grows one interval every 9 s of the owner's uptime. Each interval
carries `scheduled_open_loop` (always 100), `actual_closed_loop_sends`,
`closed_loop_send_to_response_p99_ms`,
`open_loop_scheduled_to_completion_p99_ms`, `queue_oldest_age_sec`,
`effective_worker_slots`, `completed` and `rejected`. The `dashboard_summary`
reports status green with HTTP 200 over the closed-loop series and states its
population as requests emitted after the previous closed-loop request
completed, with `selected_rows` below `expected_scheduled_arrivals`. The
`single_room_control` holds 10 worker slots, 98 of 100 completed and a p99 of
85 ms. `/metrics` on `reservation-worker` shows `queue_depth`,
`queue_oldest_age_sec` and `latency_ms` rising with uptime; on
`availability-cache` it shows `cpu_seconds_total` growing ever more slowly
with `cpu_pressure_full` at 0.002. Every signed response includes `service`,
`uptime_sec` and `signature`, and uptime is local to each service. What each
check requires is declared in `tests/test_outputs.py`, which is what the
Pytest channel reads.

## Verification

Run `bash tests/test.sh` inside the running environment. It runs
`tests/test_outputs.py` under pytest and scores the junit result against
`tests/test_weights.json`.

Format requires sorted unique lines, one colon each, a known service and a
label other than `none`. Authenticity recomputes each observation's HMAC over
its canonical JSON with the signature removed, so any edited, trimmed or
invented object fails, and every observation in both `anomalies` and
`ruled_out` must verify. The causal check reads every casebook in the anomaly's
observations, deduplicates intervals by index, and requires at least four; the
latest must show lower closed-loop sends, lower closed-loop p99, higher
open-loop p99, higher queue age, fewer effective worker slots, fewer
completions than the first, and fewer slots than the single-room control; the
latest four must show open-loop p99 and queue age strictly rising; slots must
never rise across the whole series; and the latest dashboard summary must be
green with HTTP 200 and `selected_rows` below `expected_scheduled_arrivals`.
The population check requires `selected_fraction_first` and
`selected_fraction_latest` to equal `actual_closed_loop_sends` over
`scheduled_open_loop` for the earliest and latest cited intervals within 1
percent. A recorder in
its own container samples every service's `/metrics` and `/casebook` every 5 s
to a host mount the agent cannot see.

## Scoring channels

Each run carries two numbers, and they measure different things.

**Authoritative reward.** `verifier/reward.json` is the deterministic reward
the run is graded on: `reward = (earned - penalty) / available`, clamped to
`[0, 1]`, computed only from the pytest checks in `tests/test_outputs.py`
against the weights in `tests/test_weights.json`. This is the number a machine
ingests as the reward signal, and it is fully reproducible from the repository.

**The diagnosis gate.** Naming the wrong primary service or label fails
`test_gate_expected_finding`, a `test_gate_*` check that the scorer treats as a
hard gate: if any gate check fails the reward short-circuits to exactly `0.0`
regardless of what else passed. This is deliberate. A submission with valid
format, authentic observations and cleared alternatives, but the wrong
diagnosis, scores `0.0` rather than floating on partial hygiene credit. The
same gate zeroes a near-correct run that adds a phantom finding
(`test_gate_no_phantom_findings`): a correct primary diagnosis carried
alongside an extra fabricated fault still scores `0.0`, because a report that
also blames a healthy service has not diagnosed the estate.

**The human-eval QA composite.** `score.json` and `pass_summary.json` also
carry `overall_score = 0.5 * tests_score + 0.5 * rubric_score`. The rubric half
is scored by a human evaluator (the shipped criteria and each per-criterion
justification live in `score.json`; the named grader is in the `Evaluated By`
field). It exists as a quality-assurance view, not as the reward. Because the
human rubric is not gated, `overall_score` can be non-zero on a run whose
`reward.json` is `0.0`: the rubric credits genuine partial diagnostic work -
the mechanism explanation, the two clearances, grounded reasons - even when the
verdict label is still wrong. That divergence is expected. The authoritative,
gated, deterministic channel is `reward.json`; `overall_score` is the
human-eval QA lens over the same run and is not recomputable from the repo
because it includes human judgement.

## Root cause

**Coordinated omission.** The synthetic probe is closed-loop: it waits for a
response before sending the next request. When the worker stalls, the probe
sends fewer requests, so the population it measures shrinks to the requests
that got through, and the send-to-response p99 of that population improves
as `actual_closed_loop_sends` falls from 100 towards 24 per interval. The
open-loop schedule preserves the intended arrivals, and measured from
`scheduled_at` the p99 rises by 115 ms per interval. The dashboard is a
truthful summary of the narrow population it names, and its green status and
HTTP 200 say nothing about the scheduled population.

**Where the capacity goes.** `effective_worker_slots` on the group class
falls by one per interval from 10, the single-room control keeps all 10, and
`completed` falls by 10 per interval from 94 while `queue_oldest_age_sec`
rises by 17. The worker's own `/metrics` shows queue depth, queue age and
latency rising with uptime, and a group probe against it takes up to 160 ms
longer than any other probe. The slot count is what separates
`capacity-cliff` from `queue-backlog`: a backlog with constant capacity would
not show the slots declining, and here they never recover.

**Why the cache looks guilty.** `availability-cache` CPU fell after the
change, which reads as a service that was saturated and is now relieved. Its
CPU rate follows the work completed, which is falling, and its full pressure
never leaves 0.002; a saturated service would show high full pressure and CPU
tracking demand rather than completions.

**Why the gate looks guilty.** `rejected` rises by 7 per interval from the
moment the admission policy was enabled, which reads as shedding. The gate is
doing what its published bounded policy says, the scheduled, admitted,
rejected and completed identities stay consistent across intervals, and the
completions that are lost are lost inside the worker, not at the gate.

**Why the probe looks guilty.** Its casebook shows the population gap
plainly, so `synthetic-probe:probe-population-gap` names a real phenomenon.
The probe owns the measurement, not the behaviour: the same casebook is
published by the worker and the gate, and the declining slots and rising
queue belong to the worker's own metrics. The instruction requires the
reported service to locally own the behaviour attributed to it.

**Timing.** Each service's uptime is its own clock, and a casebook reaches
four intervals only at 36 s of the owner's uptime. The window checks read
`uptime_sec` from observations whose `service` is the reported owner, so
observations from other sources do not count toward the 45 s span or the
22.5 s clearance spans.

**Red lines.** The estate is read-only; probes are permitted but at most two
in flight per service. Signed responses are evidence only whole and unedited,
and the workstation has no key to sign anything of its own. A green health
endpoint, an improving average, a lower CPU value, a large counter, a single
latest snapshot or a missing record proves neither health nor failure.

## Solution shape

1. **Verdict.** One line:

   ```
   reservation-worker:capacity-cliff
   ```

2. **Anomaly entry.** Service `reservation-worker`, class `capacity-cliff`, a
   reason of at least 40 characters stating that declining effective worker
   slots drive the falling completions and rising scheduled-to-completion
   latency while the closed-loop probe under-samples, and `observations`
   holding whole signed `/metrics` and `/casebook` responses: at least three
   from `reservation-worker` with distinct `uptime_sec` spanning 45 s or
   more, plus the probe's casebooks as the population join.
3. **Calculations.** From the earliest and latest cited intervals:

   ```
   selected_fraction_first  = actual_closed_loop_sends / scheduled_open_loop   (interval 0: 1.00)
   selected_fraction_latest = actual_closed_loop_sends / scheduled_open_loop   (interval n)
   ```

4. **Causal evidence.** Enough casebooks that the deduplicated intervals
   number at least four and show the full cliff: sends, closed-loop p99,
   slots and completions below their first values; open-loop p99 and queue
   age rising across the last four; slots never recovering and below the
   single-room control's 10; the latest dashboard green at 200 with
   `selected_rows` below `expected_scheduled_arrivals`.
5. **Clearances.** `ruled_out` entries for `availability-cache` and
   `admission-gate`, class `none`, each with two or more signed observations
   from that service spanning 22.5 s or more and a reason quoting observed
   values: the cache's falling `cpu_seconds_total` rate with
   `cpu_pressure_full` at 0.002 read as CPU following completed work; the
   gate's rising `rejected` with preserved lifecycle counts read as the
   bounded policy.
6. **Parity.** The anomaly pairs equal the verdict lines exactly; no healthy
   service, no second label, no `none` line.
7. **Red lines.** Hold every one listed above.

## Ordered plan

1. **Read `/app/doc/writing-up-a-diagnosis.md` first.** It fixes the labels,
   the evidence schema, the observation floors and the two required
   calculations, and it states that closed-loop probes wait for completion
   while open-loop probes preserve the schedule.
2. **Confirm every service is up and read `/traffic-contract`** to learn the
   four class-and-mode combinations the incident population covers.
3. **Start sampling immediately.** Fetch `/metrics` and `/casebook` from
   `synthetic-probe`, `reservation-worker`, `admission-gate` and
   `availability-cache` at intervals of 15 s or less for at least 60 s, and
   keep every response byte-for-byte as parsed JSON.
4. **Find the casebook owners.** Five services return an empty casebook; the
   three that own records publish the intervals, the dashboard summary and
   the single-room control.
5. **Order the intervals and compare the two populations.** Put the closed-
   loop send-to-response p99 beside the open-loop scheduled-to-completion
   p99, compute the selected fractions for the first and latest intervals,
   and read the dashboard's stated population before trusting its green.
6. **Attribute the capacity loss.** Match the falling `effective_worker_slots`
   and `completed` against the worker's own rising queue metrics and the
   single-room control's 10 slots.
7. **Clear the two alternatives with their own metrics.** Two or more
   observations each, 22.5 s apart or more, with the specific reading that
   clears the class it resembles.
8. **Write both artefacts and re-read them.** Check the verdict line, the
   pair parity, the owner span, the clearance spans, the calculations and
   that every observation still carries its original fields and signature.

## Pitfalls

- **Trusting the green dashboard.** It is a correct summary of the wrong
  population; `selected_rows` against `expected_scheduled_arrivals` is what
  exposes it.
- **Reading the closed-loop p99 as recovery.** It falls because fewer
  requests are sent, not because work completes faster; the open-loop p99
  rises the whole time.
- **Blaming `synthetic-probe`.** Its casebook shows the gap, but it owns the
  measurement, not the fault. `synthetic-probe:probe-population-gap` is a
  phantom at -3 and fails the no-phantom and parity checks.
- **Blaming `admission-gate` for shedding.** Rising `rejected` is the bounded
  policy working; `admission-gate:load-shedding` is a phantom and also
  drops a required clearance.
- **Blaming `availability-cache` for CPU.** Its CPU is falling and its full
  pressure is flat; `cpu-saturation` is a phantom.
- **Labelling the worker `queue-backlog`.** The queue does grow, but the
  discriminator is the declining slot count; the neighbouring label is a
  phantom and fails the expected-finding check.
- **Sampling only `/metrics`.** The causal check reads casebook intervals; a
  metrics-only anomaly has no intervals and fails the 5-weight causal and
  3-weight population checks.
- **Sampling too early or too briefly.** Fewer than four intervals means the
  owner had under 36 s of uptime; fewer than three owner observations, or a
  span under 45 s, fails the window check, and clearances need 22.5 s.
- **Counting the probe's observations toward the worker's span.** Only
  observations whose `service` is the reported owner count.
- **Trimming or reshaping observations.** Dropping fields, shortening the
  intervals list or hand-editing a value breaks the HMAC; the authenticity
  check fails and the fabrication detector subtracts 5.
- **Inventing an observation.** The workstation has no signing key, so no
  self-made object can verify.
- **Getting the fractions from the wrong intervals.** They must come from the
  earliest and latest intervals actually cited in the anomaly, within 1
  percent.
- **Writing `none`.** It fails the format check outright and every finding
  check after it.
- **Listing a healthy service in the verdict, or two labels for the worker.**
  Both break parity and add a phantom.
- **Ordering events across services by uptime.** Each clock is local; the
  casebook's own `interval` index is the ordering to use.
- **Probing hard.** More than two concurrent probes to one service is scored
  against, and probing changes nothing about the casebook.
- **Touching the estate.** Any restart, reconfiguration or patch is scored at
  -5 on the Rubric channel and destroys the evidence timeline.
