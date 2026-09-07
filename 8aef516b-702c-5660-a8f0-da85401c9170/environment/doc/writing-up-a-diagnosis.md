# Operating semantics

scheduled_at is the intended arrival time; actual_send_at is when a probe could send; admission_at and completion_at are later lifecycle points. Closed-loop probes wait for completion before sending the next request; open-loop probes preserve the declared schedule. A successful dashboard response describes only the population named in that response.

The incident population includes every combination returned by `/traffic-contract`. A valid conclusion may require matching business identity, epoch, cohort, or timing boundaries. Counters can describe attempts, calls, events, or durable outcomes; use the unit stated by the field rather than treating all rows as requests.

Health endpoints remain available during the incident. Background workload begins automatically. The complete discriminating lifecycle is observable within 60 seconds after services become ready.

# API contract

All services listen on port 8080.

- `GET /health` returns liveness only.
- `GET /metrics` returns a complete signed point-in-time service snapshot.
- `GET /casebook` returns signed lifecycle or population records owned by that service. An empty casebook means that service owns no records of that kind; it is not proof of health.
- `GET /traffic-contract` lists the supported request fields and values.
- `POST /probe` accepts one supported JSON request and returns a signed receipt. At most two probes may be in flight per service.

Supported request fields:

```json
{
  "reservation_class": [
    "single-room",
    "group"
  ],
  "probe_mode": [
    "closed-loop",
    "open-loop"
  ]
}
```

Every signed response includes `service`, `uptime_sec`, and `signature`. Preserve the entire object. Uptime is local to the service; do not order events from different services by uptime alone. Casebook event `at` values share the casebook owner's logical incident axis.

# Evidence contract

Allowed labels: `capacity-cliff`, `probe-population-gap`, `cpu-saturation`, `queue-backlog`, `slow-query`, `input-scaling`, `load-shedding`, `none`.

Write one JSON object:

```json
{
  "anomalies": [
    {
      "service": "<reported owner>",
      "class": "<allowed label>",
      "reason": "<causal conclusion>",
      "observations": [{"service": "<source>", "uptime_sec": 0, "signature": "<preserve>", "other": "<preserve every field>"}],
      "calculations": {}
    }
  ],
  "ruled_out": [
    {
      "service": "<examined alternative>",
      "class": "none",
      "reason": "<what clears it>",
      "observations": []
    }
  ]
}
```

The anomaly pairs must match the verdict exactly. Each anomaly needs three signed observations from the reported owner spanning at least 45 seconds, and may include signed observations from other owners needed for a join. Each required clearance needs at least two signed observations from that service spanning half that interval. Signatures are checked exactly; invented or edited responses are not evidence.

Use `calculations` for quantities derived from unequal populations. For this incident, include `selected_fraction_first` and `selected_fraction_latest`: divide `actual_closed_loop_sends` by `scheduled_open_loop` in the earliest and latest cited intervals. Preserve the raw observations supporting both values. A correct label without the mechanism-specific causal evidence earns only partial credit.
