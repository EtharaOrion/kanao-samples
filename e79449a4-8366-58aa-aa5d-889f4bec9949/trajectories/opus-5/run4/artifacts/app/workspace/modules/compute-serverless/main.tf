# One physical instance of the payments flow. The root composition builds two
# of these; which one serves live traffic is decided by the API Management
# backend pool weight, never by the name of this module.

resource "azurerm_storage_account" "this" {
  name                     = var.storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
  tags                     = var.tags
}

resource "azurerm_service_plan" "this" {
  name                         = "asp-paymesh-pagos${var.instance_suffix}-${var.environment}"
  resource_group_name          = var.resource_group_name
  location                     = var.location
  os_type                      = "Linux"
  sku_name                     = "EP1"
  maximum_elastic_worker_count = var.max_instance_count
  tags                         = var.tags
}

# The ip restriction below is the control the guardrail in envs/*/policies.tf
# exists to hold in place. Terraform writes it here; Azure Policy is what stops
# somebody removing it from the portal an hour later.
resource "azurerm_linux_function_app" "this" {
  name                          = "func-paymesh-pagos${var.instance_suffix}-${var.environment}"
  resource_group_name           = var.resource_group_name
  location                      = var.location
  service_plan_id               = azurerm_service_plan.this.id
  storage_account_name          = azurerm_storage_account.this.name
  storage_uses_managed_identity = true
  virtual_network_subnet_id     = var.integracion_subnet_id
  https_only                    = true
  tags                          = var.tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    ftps_state                    = "Disabled"
    minimum_tls_version           = "1.2"
    ip_restriction_default_action = "Deny"

    ip_restriction {
      name        = "azure-cloud-region"
      priority    = 100
      action      = "Allow"
      service_tag = "AzureCloud.${var.location}"
    }

    application_stack {
      dotnet_version              = "8.0"
      use_dotnet_isolated_runtime = true
    }
  }
}
