# PayMesh - policy-as-code standard

Custom Azure Policy is the only preventive control PayMesh has that keeps
working when somebody changes an environment outside Terraform. A guardrail
that is present but inert is worse than none, because the compliance sweep
reports it as present.

This document is what a change to `envs/<env>/policies.tf` and to anything
under `policies/` is reviewed against. Review reads it, not the diff.

The standard states outcomes. It does not name the arguments, the operators or
the field names that deliver them - those are published by the platform, they
change between API versions, and a reviewer who is handed one stops looking at
the rest. Working out which document produces an outcome is the work.

Review passes an environment that meets every outcome in this document and fails one that meets some of them.

---

## A. A guardrail sees what it is meant to see, and nothing else

**A1.** The definition that protects the payments flow evaluates writes to both
payments Function Apps, `func-paymesh-pagos-<env>` and
`func-paymesh-pagos-canary-<env>`, and evaluates writes to nothing else.

The same resource group holds an App Service and a workers Function App that
were never designed with this control. A definition wide enough to catch them
refused their first deployment once already, and that is not a mistake this
team makes twice.

**A2.** The definition reads the setting on the object that holds it, using the
name the platform publishes for that object.

**A3.** A matcher the platform constrains is used inside that constraint.

**A4.** The payments flow also keeps a `blue` deployment slot under each of
the two Function Apps. The slot guardrail evaluates the `blue` slot of both
payments apps, follows the environment binding, refuses an open default action,
and sees no slot under a bystander.

**A5.** Every other definition in the catalogue evaluates the objects its own
description names. A definition whose scope can never be true is present and
inert.

## B. A definition the environment owns is a definition the environment enforces

**B1.** Every entry in the catalogue carries an assignment at subscription
scope. A definition with no assignment exists in the subscription, evaluates
nothing, and is counted as a control by the sweep.

**B2.** Every definition runs in the evaluation mode that matches the objects
its rule targets. A definition in the wrong mode either skips objects its rule
was written for, or evaluates objects the team never meant it to evaluate.
Neither mode is the safe default.

## C. A document that names an environment is rendered, not copied

**C1.** A rule document that carries an environment slug is rendered from the
single copy under `policies/`, taking the slug from the environment's own
input. Per-environment copies drift apart, and a document loaded verbatim
never receives the slug at all - it goes to the control plane with the
placeholder still in it, where it matches nothing and is reported as a control.

**C2.** The pipeline renders the catalogue at more than one binding.

A single rendering cannot tell a document that follows its environment from one
that merely names the environment it was rendered for. The review job renders
the catalogue at least twice and compares. A pipeline that renders once cannot
see rule C1 at all, and that is why C1 went unnoticed for two quarters.

**C3.** Every document this environment sends is a document the control plane
will act on. It parses, and the action it asks for is a refusal. A definition
that only records what it saw is a report, not a guardrail, and the sweep
cannot tell the two apart.

---

## D. Shape

**D1.** The catalogue in `local.policy_definitions` is the only place a
definition is declared, and `local.assigned_policies` is the only place an
assignment is declared. Nothing is declared beside them.

**D2.** A guardrail over named workloads narrows its scope by name, never by
resource type alone.

**D3.** A rule document holds no secret material. Documents are committed and
are readable by anyone with repository access.
