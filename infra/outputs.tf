output "records_bucket" {
  value = module.records.name
}

output "functions" {
  value = module.app.function_names
}
