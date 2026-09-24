## Defect

The Terraform tree in `/app/workspace` owns the custom Azure Policy guardrails
for the PayMesh payments platform. `envs/prod/policies.tf` builds a catalogue
of seven definitions in `local.policy_definitions` from the documents under
`policies/` and assigns the ones listed in `local.assigned_policies` at
subscription scope. The tree initialises and validates cleanly, and the
compliance sweep reports the payments flow as covered, yet the ip restriction
on `func-paymesh-pagos-canary-prod` was removed from the portal with nothing
refusing it, and the last pipeline run that touched the definition recorded a
`400` from the control plane. The tree bundles 16 seeded faults plus a second
guardrail for deployment slots that repeats most of the payments faults:

1. (focal) `policies/require-ip-restriction.json` selects its targets with one
   `like` pattern carrying two wildcards, `*/sites/func-paymesh-pagos*/config/web`.
   The platform documents that a `like` pattern should carry one wildcard.
2. The same document tests `type` equals `Microsoft.Web/sites`, the parent
   site, instead of the child object `Microsoft.Web/sites/config` that holds
   the setting.
3. It reads the setting through
   `Microsoft.Web/sites/config/web.ipSecurityRestrictionsDefaultAction`, an
   alias the platform does not publish. For this setting the published alias is
   `Microsoft.Web/sites/config/ipSecurityRestrictionsDefaultAction`; `web.`
   twins exist only for older settings such as `web.minTlsVersion`. A
   definition naming an unpublished alias is refused at creation, which is a
   cause of the recorded `400`.
4. Its effect is `Audit`, which records rather than refuses, and which the
   sweep counts as a control.
5. `require_ip_restriction` runs in mode `Indexed`; once it targets the child
   config object it must run in `All`. Azure documents `Indexed` as evaluating
   "only resource types that support tags and location" and gives
   `Microsoft.Network/routeTables/routes` as a child that is not evaluated in
   it; `Microsoft.Web/sites/config` carries neither tags nor a location.
6. `require_tags` runs in `All`; its rule holds only `exists` tests on tag
   keys, so it belongs in `Indexed`. In `All` the definition also evaluates
   resource types that cannot carry tags - the tree declares
   `azurerm_subnet` - and with effect `Deny` it refuses them. Microsoft's own
   "Require a tag on resources" definition runs in `Indexed`.
7. `require_resource_group_tags` runs in `Indexed`. Its rule targets
   `Microsoft.Resources/subscriptions/resourceGroups`, and resource groups are
   the documented exception that needs `All`: in `Indexed` the definition never
   evaluates a resource group.
8. `require_tls_version` runs in `Indexed`; its rule targets
   `Microsoft.Web/sites/config`, which carries no tags or location, so it
   belongs in `All` for the same reason as fault 5.
9. `require_tls_version` scopes itself with `name like *-${environment}`. The
   `name` of a `Microsoft.Web/sites/config` resource is always `web`, so the
   condition can never be true and the definition is present and inert. It was
   copied from `require_https_only`, where `name` is the site name and the same
   condition is right.
10. `local.assigned_policies` lists only `deny_public_sql`, `require_tags` and
    `require_resource_group_tags`; `require_tls_version`, `require_https_only`,
    `require_ip_restriction` and `require_slot_ip_restriction` exist and
    evaluate nothing.
11. `require_ip_restriction` is loaded with `file()`, so the environment slug
    never reaches the repaired document.
12. `require_https_only` is loaded with `file()` although its document carries
    `*-${environment}`; the placeholder reaches the control plane verbatim.
13. `require_tls_version` is loaded with `file()` although its document carries
    a `${environment}` slug; same consequence.
14. `.github/workflows/terraform-ci.yml` renders the catalogue twice, but the
    second render is `terraform console -var environment=dev
    -var-file=prod.tfvars`. Terraform applies `-var` and `-var-file` in command
    line order, the later one wins, and `prod.tfvars` sets `environment = "prod"`,
    so the file the review step reads as `catalogue-alt` is a second prod
    rendering.
15. `require_tls_version` refuses with `not ... equals "1.2"`, so a site
    configuration set to minimum TLS `1.3` is refused as well. Its description
    names "an obsolete TLS version"; `1.3` is a supported value and not
    obsolete, and Microsoft's own TLS definitions test `greaterOrEquals "1.2"`.
16. The workflow installs Terraform with `hashicorp/setup-terraform@v3` and its
    default `terraform_wrapper: true`. The wrapper runs the real binary through
    `@actions/exec` without forwarding standard input, so
    `echo "$EXPR" | terraform console ...` never receives the expression: in CI
    the step waits until its timeout and writes nothing (reproduced with the
    wrapper's own source: 90 seconds, 0 bytes; the same line without the wrapper
    renders in 2 seconds). The render step's `continue-on-error: true` is why
    nothing reported it; the review step reads files the render never wrote.
17. (slot) `policies/require-slot-ip-restriction.json` targets the parent
    `Microsoft.Web/sites/config` type and an unpublished `web.` alias instead of
    `Microsoft.Web/sites/slots/config` and
    `Microsoft.Web/sites/slots/config/ipSecurityRestrictionsDefaultAction`,
    hardcodes `prod`, covers only the primary app's blue slot, audits rather
    than denies, runs in `Indexed` although slot configuration carries no tags
    or location, is loaded with `file()`, and is absent from the assignment
    list.

`require_https_only` is the one definition whose mode is right as shipped, and
`deny_public_sql` runs in `All`, which is Microsoft's recommended default for a
rule over `Microsoft.Sql/servers`; review accepts it in either mode. Every
outcome is graded at two environment bindings, `prod` and `dev`, so a document
that is right for the environment it was written in and wrong for the next
fails at both.

The reference repair (`solution/solve.sh`) restores the seeded values by exact
text replacement and confirms the tree with `terraform init -backend=false`,
`terraform validate` and a `terraform console` render at prod and at dev. Out
of bounds: `docs/POLICY-AS-CODE-STANDARD.md`, `policies/deny-public-sql.json`
and `policies/require-tags.json`, which `tests/test_outputs.py` pins by SHA-256;
the managed resources the tree declares; everything under `modules/`, which the
repair does not touch; and the `paymesh-platform/policy-review-action@v3` step
with its `catalogue` and `catalogue-alt` inputs.

## Load-bearing work

The reference repair lands in 6 files:

- `policies/require-ip-restriction.json` - one pattern per payments app inside
  the one-wildcard constraint, child type, published alias, effect `Deny`
- `policies/require-slot-ip-restriction.json` - the same shape over
  `Microsoft.Web/sites/slots/config`, both blue slots, the published slot
  alias, `Deny`
- `policies/require-tls-version.json` - a scope that can be true for a site
  configuration of this environment, refusing only versions below `1.2`
- `envs/prod/policies.tf` - five mode changes, four documents moved from
  `file()` to `templatefile()` with `{ environment = var.environment }`, and four
  entries added to `local.assigned_policies`
- `.github/workflows/terraform-ci.yml` - `terraform_wrapper: false` on the
  setup step, and the second render bound to a binding other than prod, still
  written to the path the review step reads as `catalogue-alt`
- `policies/require-https-only.json` - unchanged in content but must now be
  rendered rather than loaded

What each must end up doing is declared in `tests/test_outputs.py`, which is
what the Pytest channel reads. The grader never reads the diff: it renders
`local.policy_definitions` and `local.assigned_policies` with
`terraform console` in a clean copy of the tree, once with
`-var-file=prod.tfvars -var environment=prod` and once with
`-var environment=dev`, evaluates each rendered rule against synthetic
resources, and executes the workflow's own render steps.

## Verification

The verifier runs in Harbor's separate mode. Its box is built from
`tests/Dockerfile` on top of the environment image (`docker build -t
paymesh-policy-env:latest environment` first), and the `artifacts` directories
in `task.toml` - `envs/prod`, `policies`, `modules`, `docs` and `.github` - are
uploaded onto its seeded tree before `/tests/test.sh` runs. The script runs
`tests/test_outputs.py` under pytest with a JUnit report and scores the
verdicts against `tests/test_weights.json`:

```
reward = clamp(sum(weight of every PASSING test) / sum(positive weights), 0, 1)
```

Nothing gates the weighted sum. The detectors pass when the agent did the
forbidden thing, so their negative weight subtracts.

The checks are independent; each reads only its own gaps, so a partial repair
earns partial reward. The weights put the scope, enforcement and rendering work
at 1 each and the four faults that decide whether a guardrail acts at 3 or 5.
The two "reads the published setting" checks also require an open app or slot
to be refused, so a rule that matches nothing cannot pass them by using a
published alias. `test.sh` prints every check's full gap list.

The guardrail checks evaluate each rendered rule at both bindings against synthetic
resources, supporting `allOf`/`anyOf`/`not` and the `equals`, `notEquals`,
`in`, `notIn`, `like`, `notLike`, `contains`, `notContains`, `exists`, `less`,
`lessOrEquals`, `greater` and `greaterOrEquals` conditions over `type`, `id`, `name`, `fullName`, `location`, tags and
aliases. A rule using any other construct (ARM template functions, `count`)
is reported as not evaluable.

- Payments and slot guardrails: an open default action on both payments apps
  at the binding matches (both scope and setting checks); a closed one does
  not (setting checks); the same apps at the other binding,
  `func-paymesh-workers-<env>` and `app-paymesh-api-<env>` do not (scope
  check).
  Site configuration is `name = web`, `fullName = <site>/web`; slot
  configuration is `fullName = <site>/blue/web`. Scoping by `id` or by
  `fullName` both pass.
- TLS guardrail: a site configuration of any of the four apps at the binding
  with minimum TLS `1.0` or `1.1` matches (scope and version checks); `1.2`
  and `1.3` do not (version check); the other binding does not (scope check).
- Resource-group tag guardrail (scope check): an untagged or partly tagged
  `rg-paymesh-<env>` matches; a fully tagged one does not; an untagged storage
  account does not. A rule narrowed by name passes.
- No `like`/`notLike` pattern carries more than one `*` (scope check), and
  every alias check below holds, each counted in the check that owns the
  document (payments and slot in their setting checks, every other document in
  the enforced-catalogue check).
- Every field that reads `ipSecurityRestrictionsDefaultAction`,
  `minTlsVersion`, `httpsOnly` or `publicNetworkAccess` names an alias the
  platform publishes for that setting (a fixed list of 19 in
  `tests/test_outputs.py`). Other fields are not checked against a list; a
  wrong alias in a payments, slot or TLS rule already fails the behavioural
  checks, because the synthetic resources carry only published aliases.

The enforced-catalogue check covers outcome B. It checks at both bindings that
every definition is assigned, that the
distinct assignment count equals the definition count, and that each mode
matches the types its rule names: `Microsoft.Web/sites/config`,
`Microsoft.Web/sites/slots/config`, `Microsoft.Resources/subscriptions` and
`Microsoft.Resources/subscriptions/resourceGroups` need `All`; a rule with no
`type` condition needs `Indexed`; any other type accepts either. It also checks
that the tree declares exactly one policy-definition and one policy-assignment
resource and no policy set, and that the definition resource takes
`for_each`, `mode` and `policy_rule` from the catalogue and the assignment
takes `for_each` from the assignment list.

The same check covers outcome C1 and C3: the four slug-bearing documents
render differently at prod and dev, and every prod-rendered document contains
no `${`, parses and asks for `Deny`. For C2 the workflow is run twice, once
without the wrapper shim described below (in the enforced-catalogue check,
which isolates the flag order) and once as the job installs Terraform (in the
pipeline check). Each run parses the workflow, finds the review step and its
`catalogue` and `catalogue-alt` inputs (falling back to the seeded
`/tmp/catalogue-prod.txt` and `/tmp/catalogue-dev.txt` when the step is gone,
which only the Rubric channel scores), and executes every `run:` block that
invokes `terraform ... console` in a fresh initialised copy of the tree, applying
workflow, job and step `env`, `working-directory`, `shell` and simple matrix
values. When a job installs Terraform with `hashicorp/setup-terraform` and
does not set `terraform_wrapper` to `false`, its render steps run behind a shim
that, like the wrapper, gives the real binary no standard input and exposes it
as `$TERRAFORM_CLI_PATH/terraform-bin`. When the verifier runs as root the
steps run as `nobody` with a
timeout, and every process they leave behind is killed. The `catalogue` file
must decode to the grader's prod rendering; the `catalogue-alt` file must
differ from it and equal the grader's own rendering at the binding it was
rendered for, read from the slug that replaces `prod` in its documents. Any
lowercase binding other than `prod` is accepted, including a tree that drops
`environment` from `prod.tfvars` so the flag order no longer matters.

The reference repair scores 1.0 and the untouched tree scores 0.0.

## Root cause

**The mechanism.** Every document defect is a statement inside a JSON blob
that Terraform treats as an opaque string. `policy_rule` is not parsed, so
Terraform cannot tell one wildcard from two, does not know which aliases the
platform publishes or what `name` holds on a child resource, and has no
opinion on `Audit` versus `Deny` or on which TLS versions are obsolete;
`validate` has nothing to say. The CI fault is outside Terraform altogether:
the setup action's wrapper, not the render command, is what drops the input. The rest is
ordinary HCL and YAML that is valid and simply wrong: five mode strings, an
assignment list missing four names, four `file()` calls that should be
`templatefile()`, and two command-line flags in the wrong order.

**Why the naive check passes.** `policies.tf` shows seven definitions built
from a catalogue with matching names and descriptions. Rendering once at prod
shows documents that look environment-appropriate, because the committed slot
document names `prod` and the environment under review is `prod`. The
pipeline already renders twice, so reading the workflow suggests the second
binding is covered; only running the second line shows it renders prod. Run
locally, both lines work, because the wrapper only exists on the CI runner.

**Evaluation mode is derived, not stated.** The standard says a definition
runs in the mode matching the objects its rule targets and that neither mode
is a safe default. As shipped, five modes are wrong in both directions once
the documents are repaired. Setting everything to `All` fails on
`require_tags`; setting everything to `Indexed` fails on the config and
resource-group definitions.

**Two bindings.** The instruction and the standard both say every rule is
assessed at more than one binding. A pattern that hardcodes `prod` matches
both payments ids at prod and nothing at dev; a document loaded with `file()`
renders identically at both bindings; a second render whose var file comes
last is a prod render with a dev file name.

**Red lines.** The standard and the two documents whose rules are not in
question are hash-pinned and must survive byte-for-byte; their entries in
`policies.tf` are reviewed like every other; the managed resources must stay
declared; the reviewer action and its two catalogue inputs must survive.

## Solution shape

1. **Payments guardrail.** In `policies/require-ip-restriction.json`, target
   the child object with the published alias, refuse, and select each app by
   one pattern inside the constraint:

   ```
   type equals Microsoft.Web/sites/config
   anyOf:
     id like */func-paymesh-pagos-${environment}/config/web
     id like */func-paymesh-pagos-canary-${environment}/config/web
   not: Microsoft.Web/sites/config/ipSecurityRestrictionsDefaultAction equals Deny
   then effect Deny
   ```

2. **Slot guardrail.** In `policies/require-slot-ip-restriction.json`, the
   same shape over the slot child:

   ```
   type equals Microsoft.Web/sites/slots/config
   anyOf:
     id like */sites/func-paymesh-pagos-${environment}/slots/blue/config/web
     id like */sites/func-paymesh-pagos-canary-${environment}/slots/blue/config/web
   not: Microsoft.Web/sites/slots/config/ipSecurityRestrictionsDefaultAction equals Deny
   then effect Deny
   ```

3. **TLS guardrail.** In `policies/require-tls-version.json`, scope by
   something a site configuration of this environment has, and refuse only
   versions below 1.2:

   ```
   type equals Microsoft.Web/sites/config
   id like *-${environment}/config/web
   not: Microsoft.Web/sites/config/web.minTlsVersion greaterOrEquals "1.2"
   then effect Deny
   ```

4. **Rendering.** In `envs/prod/policies.tf`, load `require_ip_restriction`,
   `require_slot_ip_restriction`, `require_https_only` and
   `require_tls_version` with
   `templatefile(path, { environment = var.environment })`; the two pinned
   documents and the resource-group document stay on `file()`.

5. **Modes.** `require_tags` to `Indexed`; `require_resource_group_tags`,
   `require_tls_version`, `require_ip_restriction` and
   `require_slot_ip_restriction` to `All`; `require_https_only` stays
   `Indexed`; `deny_public_sql` may stay `All`.

6. **Assignments.** `local.assigned_policies` lists all seven keys, and
   nothing else declares a definition or assignment.

7. **Pipeline.** Turn the setup action's wrapper off, and put the var file
   first so the override wins:

   ```
   - uses: hashicorp/setup-terraform@v3
     with:
       terraform_version: 1.15.8
       terraform_wrapper: false
   ...
   echo "$EXPR" | terraform console -var-file=prod.tfvars > /tmp/catalogue-prod.txt
   echo "$EXPR" | terraform console -var-file=prod.tfvars -var environment=dev > /tmp/catalogue-dev.txt
   ```

   Calling `$TERRAFORM_CLI_PATH/terraform-bin` directly, or dropping
   `environment` from `prod.tfvars`, are accepted alternatives for the
   respective halves.

8. **Red lines.** Hold every one listed above.

## Ordered plan

1. **Read `docs/POLICY-AS-CODE-STANDARD.md` first.** It states outcomes A
   through D and says review passes only an environment that meets every
   one; it deliberately does not name operators, aliases or constraints.
2. **Run the pipeline's render lines as written** and decode both files. They
   are identical, which is the flag-order fault. Then read how the job installs
   Terraform: `hashicorp/setup-terraform` wraps the binary by default, and its
   documentation and source show what the wrapper does with standard input.
3. **Render the catalogue at two bindings yourself.** From `envs/prod`,
   `terraform init -backend=false`, then pipe the pipeline's `EXPR` through
   `terraform console -var-file=prod.tfvars -var environment=dev`. Documents
   that do not differ are loaded verbatim.
4. **Work outcome A as a unit.** Rewrite the payments and slot documents, look
   up the published alias for each setting instead of copying the `web.` form
   from the TLS document, and test each rendered selector against the resource
   id and `fullName` of all four apps at both bindings. Check that every other
   definition's scope can be true for the objects its description names, and
   that it refuses nothing its description does not name (TLS `1.3`).
5. **Work outcome B by deriving each mode from its document.** Read every
   rule's `type` condition, including `require_tags`, whose document is pinned
   but whose mode lives in `policies.tf`, and the resource-group definition.
   Fill the assignment list to seven.
6. **Work outcome C across all four slug-bearing documents.** Switch them to
   `templatefile`, confirm no `${` survives in the prod rendering, and close
   the `Audit` effects to `Deny`.
7. **Reorder the second render** so `-var-file=prod.tfvars` comes first, run
   it again, and confirm the decoded file differs from the prod render.
8. **Run `terraform validate`.**

## Pitfalls

- **Repairing the wildcard and stopping.** The definition still reads the
  parent object through an unpublished alias, only audits, is unassigned and
  runs in the wrong mode.
- **Copying `web.` from `web.minTlsVersion`.** The ip restriction setting has
  no `web.` alias.
- **Trusting the TLS document as a template.** Its `name` condition can never
  be true on a config resource.
- **Widening the pattern** to `*/func-paymesh-*/config/web` catches the
  workers app and the App Service, which review evaluates explicitly.
- **Hardcoding `prod` in a pattern** covers both apps at prod and nothing at
  dev.
- **Scoping a child resource by `name`.** On `Microsoft.Web/sites/config` it
  is `web`; use `id` or `fullName`.
- **Reading the workflow instead of running it.** The second render already
  says `environment=dev`; it still renders prod.
- **Trusting a local render for CI.** The same line that works in a shell hangs
  behind `setup-terraform`'s wrapper, which never passes standard input on.
- **Keeping `not equals "1.2"`** for TLS. It refuses sites already on `1.3`.
- **Writing the slug into the committed document as a literal** beside a
  rendered condition, or keeping per-environment copies under `policies/`.
- **Setting every definition to `All`** because the payments one needed it,
  or every definition to `Indexed` because the tag rule needed it.
- **Treating `require_tags` as out of scope** because its document is pinned.
  Its mode is in `policies.tf` and is reviewed.
- **Deriving `Indexed` for the resource-group definition** from the fact that
  resource groups carry tags; resource groups are the exception that needs
  `All`.
- **Editing a pinned document** to fix a mode trips the hash detector at -5 and
  changes nothing about the mode.
- **Leaving `"effect": "Audit"`** in either ip-restriction document.
- **Hardcoding `mode` or `policy_rule` on the definition resource** instead of
  reading the catalogue fails outcome B.
- **Dropping or replacing the review-action step** is scored against on the
  Rubric channel.
- **Declaring a separate `azurerm_policy_definition` or assignment resource**
  beside the catalogue fails the shape check.
- **Removing or renaming a managed resource** fires the -3 detector.
