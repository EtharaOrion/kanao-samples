resource "azurerm_mssql_server" "this" {
  name                          = "sql-paymesh-${var.environment}"
  resource_group_name           = var.resource_group_name
  location                      = var.location
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = false
  tags                          = var.tags

  azuread_administrator {
    login_username              = var.aad_admin_login
    object_id                   = var.aad_admin_object_id
    tenant_id                   = var.tenant_id
    azuread_authentication_only = true
  }

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_mssql_database" "this" {
  name           = "sqldb-paymesh-core-${var.environment}"
  server_id      = azurerm_mssql_server.this.id
  sku_name       = var.sku_name
  zone_redundant = var.zone_redundant
  tags           = var.tags
}
