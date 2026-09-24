# Policy-as-code: a preventive control against drift. These definitions do not
# depend on anyone remembering a convention - Azure Policy applies them in the
# control plane, including to changes that never went through Terraform.

data "azurerm_subscription" "current" {}

locals {
  # The catalogue of custom definitions this environment owns. One entry per
  # definition; `rule` holds the document exactly as the control plane receives
  # it, so what is reviewed here is what Azure evaluates.
  policy_definitions = {
    deny_public_sql = {
      mode         = "All"
      display_name = "PayMesh - deny Azure SQL reachable from the public network"
      description  = "Refuses an Azure SQL server whose publicNetworkAccess is anything other than Disabled, which keeps the PCI DSS assessment scope small."
      rule         = file("${path.module}/../../policies/deny-public-sql.json")
    }

    require_tags = {
      mode         = "All"
      display_name = "PayMesh - require the mandatory tags"
      description  = "Refuses a resource created without the 'environment' and 'data_classification' tags."
      rule         = file("${path.module}/../../policies/require-tags.json")
    }

    require_resource_group_tags = {
      mode         = "Indexed"
      display_name = "PayMesh - require the mandatory tags on resource groups"
      description  = "Refuses a resource group created without the 'environment' and 'data_classification' tags. Resource groups are where the cost allocation report, the PCI DSS scoping inventory and the on-call escalation map all start: an untagged group falls out of the monthly chargeback run, is invisible to the quarterly assessment that proves the cardholder data environment is contained, and pages nobody when something inside it breaks. The tags are therefore required at creation, not added afterwards by a clean-up script, and a group that arrives without them is refused in the control plane before any resource can be placed in it."
      rule         = file("${path.module}/../../policies/require-resource-group-tags.json")
    }

    require_tls_version = {
      mode         = "Indexed"
      display_name = "PayMesh - require TLS 1.2 on every site in this environment"
      description  = "Refuses a site configuration in this environment that will still negotiate an obsolete TLS version."
      rule         = file("${path.module}/../../policies/require-tls-version.json")
    }

    require_https_only = {
      mode         = "Indexed"
      display_name = "PayMesh - require HTTPS on every site in this environment"
      description  = "Refuses a site in this environment that will serve plaintext HTTP."
      rule         = file("${path.module}/../../policies/require-https-only.json")
    }

    # Deliberately scoped by name rather than to Microsoft.Web as a whole: the
    # same resource group holds an App Service and a workers Function App that
    # were never designed with an ip restriction, and a type-wide definition
    # would have refused their first deployment. Only the two Function Apps of
    # the payments flow carry one.
    require_ip_restriction = {
      mode         = "Indexed"
      display_name = "PayMesh - require an ip restriction on the payments Function Apps"
      description  = "Refuses a write to the payments Function Apps that leaves ipSecurityRestrictionsDefaultAction at anything other than Deny."
      rule         = file("${path.module}/../../policies/require-ip-restriction.json")
    }

    # Deployment slots are separate Azure child resources with a separate
    # policy alias. Both payments apps keep a blue slot during regional
    # failover; protecting only the parent site leaves that path outside the
    # preventive control.
    require_slot_ip_restriction = {
      mode         = "Indexed"
      display_name = "PayMesh - require an ip restriction on payments deployment slots"
      description  = "Refuses a write to either payments blue slot that leaves its default restriction action open."
      rule         = file("${path.module}/../../policies/require-slot-ip-restriction.json")
    }
  }

  # The definitions that carry an assignment at subscription scope. A
  # definition missing from this list still exists in the subscription and
  # still evaluates nothing at all.
  assigned_policies = ["deny_public_sql", "require_tags", "require_resource_group_tags"]
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
