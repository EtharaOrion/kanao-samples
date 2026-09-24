variable "environment" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "vnet_cidr" {
  type = string
}

variable "subnets" {
  type = map(string)
}

variable "tags" {
  type = map(string)
}
