variable "environment" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "sku_name" {
  type = string
}

variable "zone_redundant" {
  type = bool
}

variable "aad_admin_login" {
  type = string
}

variable "aad_admin_object_id" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "tags" {
  type = map(string)
}
