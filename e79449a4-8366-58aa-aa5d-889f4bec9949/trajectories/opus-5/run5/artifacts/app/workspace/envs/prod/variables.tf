variable "environment" {
  description = "Environment slug. Every resource name and every policy name carries it."
  type        = string
  default     = "prod"

  validation {
    condition     = can(regex("^[a-z]+$", var.environment))
    error_message = "environment is a lowercase word: prod, dev, qa."
  }
}

variable "location" {
  description = "Azure region for every resource in this environment."
  type        = string
}

variable "tenant_id" {
  description = "Entra tenant that owns the subscription."
  type        = string
}

variable "vnet_cidr" {
  description = "Address space of the environment virtual network."
  type        = string
}

variable "subnets" {
  description = "Range name to CIDR. The payments Function Apps join 'integracion'."
  type        = map(string)
}

variable "sql_sku_name" {
  description = "SKU of the core database."
  type        = string
}

variable "sql_zone_redundant" {
  description = "Whether the core database is spread across availability zones."
  type        = bool
}

variable "aad_admin_login" {
  description = "Entra group that administers the database server."
  type        = string
}

variable "aad_admin_object_id" {
  description = "Object id of the Entra group that administers the database server."
  type        = string
}

variable "serverless_max_instance_count" {
  description = "Upper bound on elastic workers for each payments Function App."
  type        = number
}
