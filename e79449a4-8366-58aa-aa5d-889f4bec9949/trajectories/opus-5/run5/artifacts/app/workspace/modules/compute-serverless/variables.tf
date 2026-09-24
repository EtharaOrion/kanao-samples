variable "environment" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "integracion_subnet_id" {
  type = string
}

variable "max_instance_count" {
  type = number
}

variable "instance_suffix" {
  description = "Empty for the stable instance, '-canary' for the candidate."
  type        = string
}

variable "storage_account_name" {
  type = string
}

variable "tags" {
  type = map(string)
}
