# Policy-as-code: a preventive control against drift. These definitions do not
# depend on anyone remembering a convention - Azure Policy applies them in the
# control plane, including to changes that never went through Terraform.

data "azurerm_subscription" "current" {}

locals {
  # The catalogue of custom definitions this environment owns. One entry per
  # definition; `rule` holds the document exactly as the control plane receives
  # it, so what is reviewed here is what Azure evaluates. Documents that carry
  # an environment slug are rendered from the single copy under policies/ with
  # this environment's own input, never copied per environment.
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

    # Site configuration is a child resource with no tags and no location of
    # its own, so the narrow mode never reaches it: this one runs in All.
    require_tls_version = {
      mode         = "All"
      display_name = "PayMesh - require TLS 1.2 on every site in this environment"
      description  = "Refuses a site in this environment that will still negotiate an obsolete TLS version."
      rule         = templatefile("${path.module}/../../policies/require-tls-version.json", { environment = var.environment })
    }

    # This one targets the site itself, which carries tags and a location.
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
    require_ip_restriction = {
      mode         = "All"
      display_name = "PayMesh - require an ip restriction on the payments Function Apps"
      description  = "Refuses a write to the payments Function Apps that leaves ipSecurityRestrictionsDefaultAction at anything other than Deny."
      rule         = templatefile("${path.module}/../../policies/require-ip-restriction.json", { environment = var.environment })
    }

    # Deployment slots are separate Azure child resources with a separate
    # policy alias. Both payments apps keep a blue slot during regional
    # failover; protecting only the parent site leaves that path outside the
    # preventive control.
    require_slot_ip_restriction = {
      mode         = "All"
      display_name = "PayMesh - require an ip restriction on payments deployment slots"
      description  = "Refuses a write to either payments blue slot that leaves its default restriction action open."
      rule         = templatefile("${path.module}/../../policies/require-slot-ip-restriction.json", { environment = var.environment })
    }
  }

  # The definitions that carry an assignment at subscription scope. A
  # definition missing from this list still exists in the subscription and
  # still evaluates nothing at all, so the catalogue is assigned in full.
  assigned_policies = keys(local.policy_definitions)
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
