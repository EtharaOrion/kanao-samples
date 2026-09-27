The Terraform tree in `/app/workspace` is the infrastructure repository for the
PayMesh payments platform. `envs/prod` is the root composition; the modules it
builds from live under `modules/`, and the custom Azure Policy documents the
environment owns live under `policies/`.

Two Function Apps run the payments flow, `func-paymesh-pagos-prod` and
`func-paymesh-pagos-canary-prod`. Neither is permanently senior to the other.
Both are written with an ip restriction that admits only the region's service
tag, and a custom policy exists so that removing that restriction outside
Terraform is refused in the control plane instead of noticed later.

The platform team reviews changes against `docs/POLICY-AS-CODE-STANDARD.md`
rather than against the diff. That document is short and it is in the tree.

Operators report a bug. During an incident last month the ip restriction was
taken off the canary Function App by hand, from the portal, to let a partner
address through. Nothing refused the change and nothing raised it afterwards;
the compliance sweep went on reporting the payments flow as covered by the
control the whole time. Separately, the release notes for the last pipeline run
that touched this definition record a `400` from the control plane, a retry
that failed the same way, and a decision to carry on because the rest of the
release applied cleanly.

The engineer who last worked on this guardrail has left the team. Nothing in
the tooling reports an error: the environment initialises and validates
cleanly, and the definition looks like what it is supposed to be.

Your task: change the tree so that this environment meets the policy-as-code
standard in full. The standard assesses every rule at more than one environment
binding, and it is explicit that a document which is right for the environment
it was written in and wrong for the next one has not met the rule. A guardrail that is present and inert is worse than none,
because the sweep counts it as a control - so a change that fixes what the
`400` complained about while leaving the guardrail unable to see anything has
not moved the environment anywhere.

Review does not read the diff. It reproduces what the pipeline's review job
does: it renders the environment's own policy catalogue offline, once for this
environment and once at a second environment binding, and reads the two
renderings. A change that looks right in the source and renders the wrong
document fails review, and a change that reaches the right rendering by an
unusual route passes it.

Constraints: `docs/POLICY-AS-CODE-STANDARD.md` is the standard, and the rules
in `policies/deny-public-sql.json` and `policies/require-tags.json` are not in
question; leave all three files byte-for-byte as they are. Their entries in
`envs/prod/policies.tf` - how each document is loaded, the mode it runs in and
whether it is assigned - are part of the catalogue and are reviewed like every
other entry. Terraform and its providers are vendored into the image, and there
is no Azure subscription here, so `plan` and `apply` are not available to you.
