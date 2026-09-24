output "server_id" {
  value = azurerm_mssql_server.this.id
}

output "database_id" {
  value = azurerm_mssql_database.this.id
}

output "database_name" {
  value = azurerm_mssql_database.this.name
}
