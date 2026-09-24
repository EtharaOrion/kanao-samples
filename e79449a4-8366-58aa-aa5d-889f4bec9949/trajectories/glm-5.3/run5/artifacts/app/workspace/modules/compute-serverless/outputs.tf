output "function_app_name" {
  value = azurerm_linux_function_app.this.name
}

output "function_app_id" {
  value = azurerm_linux_function_app.this.id
}

output "principal_id" {
  value = azurerm_linux_function_app.this.identity[0].principal_id
}
