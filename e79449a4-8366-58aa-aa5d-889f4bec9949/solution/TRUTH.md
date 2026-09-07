## Defect

The Terraform tree in `/app/workspace` owns the custom Azure Policy guardrails
for the PayMesh payments platform. `envs/prod/policies.tf` builds a catalogue
of six definitions in `local.policy_definitions` from the documents under
`policies/` and assigns the ones listed in `local.assigned_policies` at
subscription scope. The tree initialises and validates cleanly, and the
compliance sweep reports the payments flow as covered, yet the ip restriction
on `func-paymesh-pagos-canary-prod` was removed from the portal with nothing
refusing it, and the last pipeline run that touched the definition recorded a
`400` from the control plane. The tree bundles 13 seeded fault(s) plus a
second guardrail for deployment slots that repeats most of them:

1. (focal) `policies/require-ip-restriction.json` selects its targets with one
   `like` pattern carrying two wildcards, `*/sites/func-paymesh-pagos*/config/web`.
   The operator accepts one wildcard, so the control plane refuses the whole
   definition with the `400` and whatever was live stays live.
2. The same document tests `type` equals `Microsoft.Web/sites`, the parent
   site, instead of the child object `Microsoft.Web/sites/config` that holds
   the setting.
3. It reads the setting through
   `Microsoft.Web/sites/siteConfig.ipSecurityRestrictionsDefaultAction`, an
   alias the platform does not publish; the published alias is
   `Microsoft.Web/sites/config/web.ipSecurityRestrictionsDefaultAction`.
4. Its effect is `Audit`, which records rather than refuses, and which the
   sweep counts as a control.
5. `require_ip_restriction` runs in mode `Indexed`; once it targets the child
   config object it must run in `All`.
6. `require_tags` runs in `All`; its rule holds only `exists` tests on tag
   keys, so it belongs in `Indexed`.
7. `deny_public_sql` runs in `All`; its rule names only `Microsoft.Sql/servers`,
   a top-level type, so it belongs in `Indexed`.
8. `require_tls_version` runs in `Indexed`; its rule targets
   `Microsoft.Web/sites/config`, so it belongs in `All`.
9. `local.assigned_policies` lists only `deny_public_sql` and `require_tags`;
   `require_tls_version`, `require_https_only`, `require_ip_restriction` and
   `require_slot_ip_restriction` exist and evaluate nothing.
10. `require_ip_restriction` is loaded with `file()`, so the environment slug
    never reaches the repaired document.
11. `require_https_only` is loaded with `file()` although its document carries
    `*-${environment}`; the placeholder reaches the control plane verbatim.
12. `require_tls_version` is loaded with `file()` although its document carries
    `*-${environment}/config/web`; same consequence.
13. `.github/workflows/terraform-ci.yml` renders the catalogue once, at the
    prod binding, while the review step reads a `catalogue-alt` file that no
    step produces.
14. (slot) `policies/require-slot-ip-restriction.json` targets the parent
    `Microsoft.Web/sites/config` type and alias instead of
    `Microsoft.Web/sites/slots/config` and its slot alias, hardcodes `prod`,
    covers only the primary app's blue slot, audits rather than denies, runs in
    `Indexed`, is loaded with `file()`, and is absent from the assignment
    list.

`require_https_only` is the one definition whose mode is right as shipped.
Every outcome is graded at two environment bindings, `prod` and `dev`, so a
document that is right for the environment it was written in and wrong for the
next fails at both.

The reference repair (`solution/solve.sh`) restores the thirteen values by
exact text replacement, rewrites the slot document, and confirms the tree with
`terraform init -backend=false`, `terraform validate` and one
`terraform console` render. Out of bounds: `docs/POLICY-AS-CODE-STANDARD.md`,
`policies/deny-public-sql.json` and `policies/require-tags.json`, which
`tests/test_outputs.py` pins by SHA-256; everything under `modules/`; the
`paymesh-platform/policy-review-action@v3` step and its `catalogue` and
`catalogue-alt` inputs; and the files under `/tests`.

## Load-bearing work

The reference repair lands in 6 file(s):

- `policies/require-ip-restriction.json` - one pattern per payments app inside
  the one-wildcard constraint, child type, published alias, effect `Deny`
- `policies/require-slot-ip-restriction.json` - the same shape over
  `Microsoft.Web/sites/slots/config`, both blue slots, slot alias, `Deny`
- `envs/prod/policies.tf` - five mode changes, four documents moved from
  `file()` to `templatefile()` with `{ environment = var.environment }`, and
  four entries added to `local.assigned_policies`
- `.github/workflows/terraform-ci.yml` - a second `terraform console` render
  at a binding other than prod, written to the path the review step reads as
  `catalogue-alt`
- `policies/require-https-only.json` and `policies/require-tls-version.json`
  - unchanged in content but must now be rendered rather than loaded

What each must end up doing is declared in `tests/test_outputs.py`, which is
what the Pytest channel reads. The grader never reads the diff: it renders
`local.policy_definitions` and `local.assigned_policies` with
`terraform console` in a clean copy of the tree, once with `-var-file=prod.tfvars`
and once with `-var environment=dev` added, and evaluates the two renderings.

## Verification

Run `bash tests/test.sh` inside the built environment. It first gates: the
directory `envs/prod` must contain `.tf` files, the tree must still declare
the ten managed resource types (policy definition, policy assignment, resource
group, virtual network, subnet, service plan, Linux function app, storage
account, SQL server, SQL database), and `terraform init -backend=false` plus
`terraform validate` must pass; any gate failure scores 0. It then runs
`tests/test_outputs.py` under pytest and scores the verdicts against
`tests/test_weights.json`.

Pytest channel, 5 checks, 11 positive weight:

| Check | Weight |
|---|---|
| outcome A: guardrails see their targets under review | 5 |
| outcome B: every definition is enforced under review | 1 |
| outcome C: documents follow their binding under review | 5 |
| verifier package was removed (detector) | -5 |
| policy inputs were altered (detector) | -3 |

The three positive checks are conjunctive: each asserts its own outcome and
then asserts that neither of the other two is unmet, so they pass together or
fail together and a partial repair scores 0. Outcome A checks, at both
bindings, that the ip-restriction and slot documents carry no pattern with
more than one wildcard, that their rendered patterns match the concrete ids of
`func-paymesh-pagos-<env>` and `func-paymesh-pagos-canary-<env>` (and their
blue slots) and do not match `func-paymesh-workers-<env>` or
`app-paymesh-api-<env>`, that each targets its child type and published alias,
and that no document in the catalogue carries a two-wildcard pattern. Outcome
B checks that every definition is assigned, that the distinct assignment count
equals the definition count, that each mode is `All` when the document's own
`type` condition names a child type and `Indexed` otherwise, that the tree
declares exactly one policy-definition and one policy-assignment resource and
no policy-set, and that no per-environment copy such as `*-dev.json` sits
under `policies/`. Outcome C checks that the pipeline file contains at least
two `terraform console` renders with one bound to an environment other than
the one in `prod.tfvars` (accepting `-var`, `-var=`, `-var-file=<env>.tfvars`
or `TF_VAR_environment`), that the `catalogue-alt` path appears on that second
render line, that the four slug-bearing documents render differently at prod
and dev, and that every prod-rendered document contains no `${`, parses, and
asks for `Deny`. The reward is the credited weight over 11, clamped to
[0, 1], written to `/logs/verifier/reward.txt`; no pytest verdicts at all
exits 3 rather than scoring. The reference repair scores 1.0 and the untouched
tree scores 0.0.

Rubric channel: `tests/rubric.json` holds 18 judged criteria over the
trajectory and the final state. Positive criteria credit deriving each mode
from its own rule's target (`deny_public_sql`, `require_tags`,
`require_https_only`), separating a definition's mode in `policies.tf` from
the protected document, asserting rendered modes against targets in the
agent's own check, testing the rendered pattern against a bystander id,
attributing the `400` to a multi-wildcard `like`, stating what the narrow
mode covers, reporting the unrendered placeholder in `require_tls_version`,
keeping the prod render when adding the second, rendering every slug-bearing
document through `templatefile` from its single copy, leaving `modules/`
byte-identical, and identifying the slot guardrail's parent-type fault.
Negative criteria penalise a final message that reports every mode correct
while `require_tags` is still `All`, calling `deny_public_sql` out of scope on
the strength of the protected-file sentence, editing
`policies/deny-public-sql.json` to change a mode, dropping the review-action
step, and writing the slug as a literal beside a rendered condition.

## Root cause

**The mechanism.** Every document defect is a statement inside a JSON blob
that Terraform treats as an opaque string. `policy_rule` is not parsed, so
Terraform cannot tell one wildcard from two, does not know which aliases the
platform publishes, and has no opinion on `Audit` versus `Deny`; `validate`
has nothing to say. The rest is ordinary HCL that is valid and simply wrong:
five mode strings, an assignment list missing four names, three `file()` calls
that should be `templatefile()`, and one deleted pipeline line.

**Why the naive check passes.** `policies.tf` shows six definitions built
from a catalogue with matching names and descriptions. Rendering once at prod
shows documents that look environment-appropriate, because the committed slot
document names `prod` and the environment under review is `prod`; that is
exactly what the shipped pipeline does, which is why nothing caught it. The
two documents loaded verbatim with a `${environment}` placeholder still parse
as JSON, so they reach the control plane, match nothing, and are reported as
controls.

**Evaluation mode is derived, not stated.** The standard says a definition
runs in the mode matching the objects its rule targets and that neither mode
is a safe default. The grader reads each rendered document's own `type`
condition: a child type such as `Microsoft.Web/sites/config` needs `All`, a
top-level type or a tag-only rule needs `Indexed`. As shipped, four of six
modes are wrong in both directions. Repairing the payments document to the
child type flips its required mode as well, so the complete repair changes
five. Setting everything to `All` fails, and so does setting everything to
`Indexed`.

**Two bindings.** The instruction and the standard both say every rule is
assessed at more than one binding. A pattern that hardcodes `prod` matches
both payments ids at prod and nothing at dev; a document loaded with `file()`
renders identically at both bindings, which is what outcome C detects. The
pipeline condition lives in a YAML file outside the Terraform tree.

**The unmentioned fifth definition.** `require_tls_version` is not in the
brief. It is unassigned, loaded verbatim with its placeholder, and in the
wrong mode; a repair that follows the brief's two named apps and the two
documents they point at never reaches it, and outcome B and C both fail on it.

**Red lines.** The standard and the two guardrails not in question are
hash-pinned and must survive byte-for-byte; `modules/` is outside the repair
surface; the reviewer action and its two catalogue inputs must survive; the
files under `/tests` must not be removed.

## Solution shape

1. **Payments guardrail.** In `policies/require-ip-restriction.json`, target
   the child object with the published alias, refuse, and select each app by
   one pattern inside the constraint:

   ```
   type equals Microsoft.Web/sites/config
   anyOf:
     id like */func-paymesh-pagos-${environment}/config/web
     id like */func-paymesh-pagos-canary-${environment}/config/web
   not: Microsoft.Web/sites/config/web.ipSecurityRestrictionsDefaultAction equals Deny
   then effect Deny
   ```

2. **Slot guardrail.** In `policies/require-slot-ip-restriction.json`, the
   same shape over the slot child:

   ```
   type equals Microsoft.Web/sites/slots/config
   anyOf:
     id like */sites/func-paymesh-pagos-${environment}/slots/blue/config/web
     id like */sites/func-paymesh-pagos-canary-${environment}/slots/blue/config/web
   not: Microsoft.Web/sites/slots/config/web.ipSecurityRestrictionsDefaultAction equals Deny
   then effect Deny
   ```

3. **Rendering.** In `envs/prod/policies.tf`, load `require_ip_restriction`,
   `require_slot_ip_restriction`, `require_https_only` and
   `require_tls_version` with
   `templatefile(path, { environment = var.environment })`; the two
   protected documents stay on `file()`.

4. **Modes.** `deny_public_sql` and `require_tags` to `Indexed`;
   `require_tls_version`, `require_ip_restriction` and
   `require_slot_ip_restriction` to `All`; `require_https_only` stays
   `Indexed`. Property to hold at both bindings:

   ```
   for each key in local.policy_definitions:
       child := rendered rule's type condition names a type with two or more slashes
       assert mode == (child ? "All" : "Indexed")
   ```

5. **Assignments.** `local.assigned_policies` lists all six keys, and nothing
   else declares a definition or assignment.

6. **Pipeline.** Keep the prod render and add a second render at another
   binding writing to the path the review step reads:

   ```
   echo "$EXPR" | terraform console -var-file=prod.tfvars > /tmp/catalogue-prod.txt
   echo "$EXPR" | terraform console -var-file=prod.tfvars -var environment=dev > /tmp/catalogue-dev.txt
   ```

7. **Red lines.** Hold every one listed above.

## Ordered plan

1. **Read `docs/POLICY-AS-CODE-STANDARD.md` first.** It states outcomes A
   through D and says review passes only an environment that meets every
   one; it deliberately does not name operators, aliases or constraints.
2. **Render the catalogue at two bindings before editing.** From `envs/prod`,
   `terraform init -backend=false`, then pipe the pipeline's `EXPR` through
   `terraform console -var-file=prod.tfvars` with and without
   `-var environment=dev`. Decode and diff the two; documents that do not
   differ are loaded verbatim.
3. **Work outcome A as a unit.** Rewrite the payments and slot documents,
   then test each rendered `like` pattern against concrete ids for all four
   apps at both bindings, including the workers app and the App Service.
4. **Work outcome B by deriving each mode from its document.** Read every
   rule's `type` condition, including `deny_public_sql` and `require_tags`
   whose documents are protected but whose modes live in `policies.tf`, and
   fill the assignment list to six.
5. **Work outcome C across all four slug-bearing documents.** Switch them to
   `templatefile`, confirm no `${` survives in the prod rendering, and close
   the `Audit` effects to `Deny`.
6. **Add the second render to the pipeline** beside the existing one, writing
   `/tmp/catalogue-dev.txt`, and leave the review step intact.
7. **Render both bindings again and run `terraform validate`.**

## Pitfalls

- **Repairing the wildcard and stopping.** The `400` goes away, but the
  definition still reads the parent object through an unpublished alias, only
  audits, is unassigned and runs in the wrong mode.
- **Widening the pattern** to `*/func-paymesh-*/config/web` catches the
  workers app and the App Service, which the check evaluates explicitly.
- **Hardcoding `prod` in a pattern** covers both apps at prod and nothing at
  dev; every outcome is evaluated at `dev` too.
- **Rendering once** cannot distinguish a document that follows the
  environment from one that happens to name it.
- **Writing the slug into the committed document as a literal** beside a
  rendered condition, or keeping per-environment copies under `policies/`, is
  scored against on both channels.
- **Setting every definition to `All`** because the payments one needed it
  fails outcome B; so does setting every definition to `Indexed`.
- **Treating `deny_public_sql` as out of scope** because its document is
  protected. Its mode is in `policies.tf`, which is not protected, and it is
  wrong.
- **Editing a protected document** to fix a mode or anything else trips the
  hash detector at -3 and changes nothing about the mode.
- **Leaving `"effect": "Audit"`** in either ip-restriction document; the
  sweep reports it as a control and outcome C fails.
- **Never noticing `require_tls_version`**, which the brief does not mention
  and which is unassigned, loaded verbatim and in the wrong mode.
- **Leaving the pipeline rendering one binding.** It is a YAML file, not
  Terraform, and it carries a graded condition; the second render must be
  bound to a non-prod environment and write the `catalogue-alt` path.
- **Dropping or replacing the review-action step** while editing the
  workflow is scored against on the Rubric channel.
- **Declaring a separate `azurerm_policy_definition` or assignment resource**
  beside the catalogue fails the shape check even if the documents are right.
- **Touching anything under `modules/`** is scored against; the repair lives
  in `envs/prod/policies.tf`, `policies/` and the workflow file.
- **Emptying or removing `.tf` files** to make validation pass hits the
  declaration gate and scores 0 before any check runs.
