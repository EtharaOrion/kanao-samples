# environment is not pinned here on purpose. It defaults to "prod" in
# variables.tf, so this file still renders the prod catalogue on its own, and a
# binding the review job passes with -var reaches the documents whichever order
# the flags are given in - pinned here it would override that binding and both
# renderings would come out identical.
location  = "eastus2"
tenant_id = "0f9c1b6e-2b1a-4f2e-9c1b-6e2b1a4f2e9c"

vnet_cidr = "10.40.0.0/16"

subnets = {
  aplicacion  = "10.40.1.0/24"
  integracion = "10.40.2.0/24"
  datos       = "10.40.3.0/24"
}

sql_sku_name       = "P2"
sql_zone_redundant = true

aad_admin_login     = "grp-paymesh-sql-admins"
aad_admin_object_id = "7a1d3c55-9e44-4b0a-8d21-33c9f0a15b62"

serverless_max_instance_count = 20
