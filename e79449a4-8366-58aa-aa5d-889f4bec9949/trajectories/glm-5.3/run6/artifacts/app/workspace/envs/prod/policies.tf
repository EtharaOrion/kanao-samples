# Policy-as-code: a preventive control against drift. These definitions do not
# depend on anyone remembering a convention - Azure Policy applies them in the
# control plane, including to changes that never went through Terraform.

data "azurerm_subscription" "current" {}

locals {
  # The catalogue of custom definitions this environment owns. One entry per
  # definition; `rule` holds the document exactly as the control plane receives
  # it, so what is reviewed here is what Azure evaluates.
  #
  # Documents that carry the environment slug are rendered from the single copy
  # under policies/, taking the slug from this environment's own input. A
  # document loaded verbatim keeps the placeholder, goes to the control plane
  # with it still inside, matches nothing, and is still counted as a control.
  #
  # Modes follow what each rule reads: a definition that reads a site object
  # runs in Indexed, one that reads a config or slot child, or a resource
  # group, runs in All, because Indexed never evaluates those objects at all.
  policy_definitions = {
    deny_public_sql = {
      mode         = "Indexed"
      display_name = "PayMesh - deny Azure SQL reachable from the public network"
      description  = "Refuses an Azure SQL server whose publicNetworkAccess is anything other than Disabled, which keeps the PCI DSS assessment scope small."
      rule         = file("${path.module}/../../policies/deny-public-sql.json")
    }

    require_tags = {
      mode         = "Indexed"
      display_name = "PayMesh - require the mandatory tags"
      description  = "Refuses a resource created without the 'environment' and 'data_classification' tags."
      rule         = file("${path.module}/../../policies/require-tags.json")
    }

    require_resource_group_tags = {
      mode         = "All"
      display_name = "PayMesh - require the mandatory tags on resource groups"
      description  = "Refuses a resource group created without the 'environment' and 'data_classification' tags. Resource groups are where the cost allocation report, the PCI DSS scoping inventory and the on-call escalation map all start: an untagged group falls out of the monthly chargeback run, is invisible to the quarterly assessment that proves the cardholder data environment is contained, and pages nobody when something inside it breaks. The tags are therefore required at creation, not added afterwards by a clean-up script, and a group that arrives without them is refused in the control plane before any resource can be placed in it."
      rule         = file("${path.module}/../../policies/require-resource-group-tags.json")
    }

    require_tls_version = {
      mode         = "All"
      display_name = "PayMesh - require TLS 1.2 on every site in this environment"
      description  = "Refuses a site configuration in this environment that will still negotiate an obsolete TLS version."
      rule         = templatefile("${path.module}/../../policies/require-tls-version.json", { environment = var.environment })
    }

    require_https_only = {
      mode         = "Indexed"
      display_name = "PayMesh - require HTTPS on every site in this environment"
      description  = "Refuses a site in this environment that will serve plaintext HTTP."
      rule         = templatefile("${path.module}/../../policies/require-https-only.json", { environment = var.environment })
    }

    # Deliberately scoped by name rather than to Microsoft.Web as a whole: the
    # same resource group holds an App Service and a workers Function App that
    # were never designed with an ip restriction, and a type-wide definition
    # would have refused their first deployment. Only the two Function Apps of
    # the payments flow carry one.
    #
    # The rule reads the setting on both objects that can carry it: the site
    # write, through the siteConfig alias the platform publishes for the site,
    # and the web config write beneath it, through the config alias. Either
    # path that leaves the default action open is refused in the control plane.
    require_ip_restriction = {
      mode         = "All"
      display_name = "PayMesh - require an ip restriction on the payments Function Apps"
      description  = "Refuses a write to the payments Function Apps that leaves ipSecurityRestrictionsDefaultAction at anything other than Deny."
      rule         = templatefile("${path.module}/../../policies/require-ip-restriction.json", { environment = var.environment })
    }

    # Deployment slots are separate Azure child resources with a separate
    # policy alias. Both payments apps keep a blue slot during regional
    # failover; protecting only the parent site leaves that path outside the
    # preventive control. The rule reads the slot write and the web config
    # write beneath the blue slot, and follows the environment binding.
    require_slot_ip_restriction = {
      mode         = "All"
      display_name = "PayMesh - require an ip restriction on payments deployment slots"
      description  = "Refuses a write to either payments blue slot that leaves its default restriction action open."
      rule         = templatefile("${path.module}/../../policies/require-slot-ip-restriction.json", { environment = var.environment })
    }
  }

  # The definitions that carry an assignment at subscription scope. A
  # definition missing from this list still exists in the subscription and
  # still evaluates nothing at all.
  assigned_policies = [
    "deny_public_sql",
    "require_tags",
    "require_resource_group_tags",
    "require_tls_version",
    "require_https_only",
    "require_ip_restriction",
    "require_slot_ip_restriction",
  ]
}

resource "azurerm_policy_definition" "this" {
  for_each = local.policy_definitions

  name         = "paymesh-${replace(each.key, "_", "-")}-${var.environment}"
  policy_type  = "Custom"
  mode         = each.value.mode
  display_name = each.value.display_name
  description  = each.value.description

  policy_rule = each.value.rule
}

resource "azurerm_subscription_policy_assignment" "this" {
  for_each = toset(local.assigned_policies)

  name                 = "paymesh-${replace(each.key, "_", "-")}-${var.environment}"
  policy_definition_id = azurerm_policy_definition.this[each.key].id
  subscription_id      = data.azurerm_subscription.current.id
  description          = local.policy_definitions[each.key].description
  display_name         = local.policy_definitions[each.key].display_name
}
