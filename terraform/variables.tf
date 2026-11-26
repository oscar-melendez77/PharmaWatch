variable "gcp_project_id" {
  description = "ID of the Google Cloud project that will hold everything"
  type        = string
}

variable "gcp_region" {
  description = "Region for the bucket and the BigQuery datasets, both must be in the same place"
  type        = string
  default     = "us-central1"
}

variable "github_repository" {
  description = "The GitHub repository allowed to sign in to Google Cloud, written as owner/name"
  type        = string
  default     = "oscar-melendez77/PharmaWatch"
}

variable "cloud_run_runtime_service_account" {
  description = "Email of the service account the Cloud Run service runs as, the deployer must be allowed to act as it"
  type        = string
  default     = "595795276542-compute@developer.gserviceaccount.com"
}
