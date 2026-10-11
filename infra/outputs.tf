output "ecr_repository_url" {
  value = aws_ecr_repository.gtb.repository_url
}

output "records_bucket" {
  value = aws_s3_bucket.records.bucket
}

output "functions" {
  value = [for f in aws_lambda_function.run : f.function_name]
}
