# paymesh-iac-terraform

Infrastructure for the PayMesh digital payments platform. One Terraform
composition per environment under `envs/`, reusable modules under `modules/`,
and the custom Azure Policy documents the environments own under `policies/`.

## Layout

```
envs/prod/          root composition for the prod environment
  versions.tf       provider constraints, backend (supplied by the pipeline)
  variables.tf      inputs, all documented
  prod.tfvars       the values this environment runs with
  main.tf           resource group and the module composition
  policies.tf       the policy catalogue and the resources that create it
  outputs.tf        what leaves the environment
modules/            networking, data-sql, compute-serverless
policies/           rule documents, one file per definition
docs/               the standards review reads
```

## The payments flow

Two physically separate Function Apps run the payments flow:
`func-paymesh-pagos-<env>` and `func-paymesh-pagos-canary-<env>`. Neither is
permanently senior to the other - which one serves live traffic is decided by
the API Management backend pool weight at the time. Both carry an ip
restriction that admits only the region's `AzureCloud` service tag.

That restriction is written by Terraform and held in place by Azure Policy, so
that removing it from the portal is refused in the control plane rather than
noticed on the next plan.

Each payments app also keeps a `blue` deployment slot for regional failover.
The release pipeline creates the slots, so they are not declared under
`modules/`, but the slot configuration is covered by its own guardrail.

The same resource group holds two workloads outside the payments flow,
`func-paymesh-workers-<env>` and `app-paymesh-api-<env>`. Their teams deploy
them from their own repositories, and neither carries an ip restriction.

## Working on this repository

```
cd envs/prod
terraform init -backend=false -input=false
terraform validate
```

There is no subscription attached to a review checkout, so `plan` and `apply`
are pipeline-only. What a review can do offline is read the catalogue the
environment builds:

```
echo 'local.policy_definitions' | terraform console -var-file=prod.tfvars
echo 'local.assigned_policies'  | terraform console -var-file=prod.tfvars
```

`terraform console` renders the same documents the pipeline sends to the
control plane.

Changes are reviewed against `docs/POLICY-AS-CODE-STANDARD.md`.
