# Only what has to leave this environment for another system. Everything the
# modules consume from each other stays inside the composition.

output "payments_function_app_names" {
  description = "The two Function Apps of the payments flow, in the order the release pipeline ramps them."
  value = [
    module.compute_serverless.function_app_name,
    module.compute_serverless_canary.function_app_name,
  ]
}

output "policy_definition_names" {
  description = "Custom definition names this environment owns, for the compliance sweep to look up."
  value       = { for k, v in azurerm_policy_definition.this : k => v.name }
}
