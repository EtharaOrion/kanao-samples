A reservation slowdown was reported during peak traffic. Operators enabled an admission-control protection shortly afterward.

The service dashboards improved after the change. Reported latency dropped, CPU usage decreased, and health checks stayed green. Customer-facing signals did not improve in the same way. Some guests experienced long waits before their requests were processed, reservation completions continued to decline, and larger bookings appear to be affected more often.

The teams responsible for the edge, admission layer, cache, and workers have not established where the problem originates.

Investigate the current estate and determine whether the apparent recovery reflects the experience of the traffic the platform is expected to serve. Identify any service that is actually violating its documented behavior, and clear services whose behavior is consistent with their contracts.

Do not assume that a healthy endpoint, improving latency average, low CPU value, large counter, latest snapshot, or missing record proves either health or failure.

The estate contains:

`synthetic-probe`, `reservation-edge`, `admission-gate`, `reservation-worker`, `availability-cache`, `inventory`, `confirmation`, `user-wait-log`.

Each service is available on port `8080` using its service name and exposes:

`/health`
`/metrics`
`/casebook`
`/traffic-contract`
`/probe`

Read `/app/doc/writing-up-a-diagnosis.md` before submitting a diagnosis. It defines the supported labels, evidence format, API behavior, and output schema.

This is a read-only investigation. Do not restart services, modify configuration, patch workloads, or otherwise change the estate. Ordinary diagnostic `POST` requests to `/probe` are permitted. Use no more than two concurrent probes against any individual service.

For every reported fault, establish that the reported service locally owns the behavior you are attributing to it. Use repeated observations rather than a single snapshot. Each finding must include at least three complete signed observations produced by the reported service. The earliest and latest qualifying observations must span at least 45 seconds of that service's uptime.

Include the lifecycle or traffic-population evidence needed to show where the behavior occurs and which service owns it. If you investigate a plausible alternative owner, collect enough repeated evidence to show why it does not explain the observed failure.

Do not alter, shorten, reconstruct, or manually rewrite signed responses included as evidence.

Write the final service classifications to:

`/app/verdict.txt`

Use one line per supported fault:

`service:label`

Use only labels defined by the provided documentation. Sort the lines alphabetically. Do not report healthy services. If no service in the estate is faulty, write exactly:

`none`

Also write:

`/app/monitor_evidence.json`

using the schema defined in `/app/doc/writing-up-a-diagnosis.md`.

The submitted evidence, signatures, observation intervals, ownership relationships, and diagnosis predicates will be validated.
