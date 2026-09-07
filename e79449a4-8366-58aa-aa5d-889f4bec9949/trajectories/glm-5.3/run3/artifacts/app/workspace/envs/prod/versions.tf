terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.80"
    }
  }

  # Remote state lives in the platform subscription. The block is commented out
  # here because the pipeline supplies it with -backend-config=backend.hcl, and
  # because review initialises this environment with no backend at all.
  # backend "azurerm" {}
}

provider "azurerm" {
  features {}
}
