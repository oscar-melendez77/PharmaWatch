output "raw_bucket_name" {
  description = "Name of the raw data lake bucket"
  value       = google_storage_bucket.raw.name
}

output "raw_bucket_url" {
  description = "gs:// address of the raw bucket"
  value       = google_storage_bucket.raw.url
}

output "bigquery_raw_dataset" {
  description = "Dataset for raw loaded tables"
  value       = google_bigquery_dataset.raw.dataset_id
}

output "bigquery_staging_dataset" {
  description = "Dataset for dbt staging models"
  value       = google_bigquery_dataset.staging.dataset_id
}

output "bigquery_marts_dataset" {
  description = "Dataset for dbt marts"
  value       = google_bigquery_dataset.marts.dataset_id
}

output "pipeline_service_account" {
  description = "Email of the service account the pipeline runs as"
  value       = google_service_account.pipeline.email
}

output "github_workload_identity_provider" {
  description = "Provider name the GitHub workflow uses to sign in"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  description = "Email of the service account that deploys the dashboard"
  value       = google_service_account.deployer.email
}

output "image_repository" {
  description = "Address of the Artifact Registry repository for dashboard images"
  value       = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${google_artifact_registry_repository.images.repository_id}"
}
