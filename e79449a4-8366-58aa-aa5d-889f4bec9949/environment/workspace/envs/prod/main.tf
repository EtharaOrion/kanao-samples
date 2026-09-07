# Root composition of the prod environment. Every module is instantiated from
# here; the modules themselves hold no environment-specific values.

# Mandatory tags are injected centrally so no module can omit them.
# policies/require-tags.json enforces the same two keys in the control plane.
locals {
  common_tags = {
    environment         = var.environment
    owner               = "plataforma-paymesh"
    cost_center         = "pagos-digitales"
    data_classification = "confidential-pci"
  }
}

resource "azurerm_resource_group" "this" {
  name     = "rg-paymesh-${var.environment}"
  location = var.location
  tags     = local.common_tags
}

module "networking" {
  source = "../../modules/networking"

  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  vnet_cidr           = var.vnet_cidr
  subnets             = var.subnets
  tags                = local.common_tags
}

module "data_sql" {
  source = "../../modules/data-sql"

  environment         = var.environment
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  sku_name            = var.sql_sku_name
  zone_redundant      = var.sql_zone_redundant
  aad_admin_login     = var.aad_admin_login
  aad_admin_object_id = var.aad_admin_object_id
  tenant_id           = var.tenant_id
  tags                = local.common_tags
}

# The stable half of the payments flow. Its name is what the guardrail in
# policies.tf matches on, so the two have to move together.
module "compute_serverless" {
  source = "../../modules/compute-serverless"

  environment           = var.environment
  location              = var.location
  resource_group_name   = azurerm_resource_group.this.name
  integracion_subnet_id = module.networking.subnet_ids["integracion"]
  max_instance_count    = var.serverless_max_instance_count
  instance_suffix       = ""
  storage_account_name  = "stpaymeshpagos${var.environment}"
  tags                  = local.common_tags
}

# The candidate half. A dedicated plan and storage account, never shared with
# the stable half, so a bad deployment of one cannot reach the other.
module "compute_serverless_canary" {
  source = "../../modules/compute-serverless"

  environment           = var.environment
  location              = var.location
  resource_group_name   = azurerm_resource_group.this.name
  integracion_subnet_id = module.networking.subnet_ids["integracion"]
  max_instance_count    = var.serverless_max_instance_count
  instance_suffix       = "-canary"
  storage_account_name  = "stnpypagoscanary${var.environment}"
  tags                  = local.common_tags
}
