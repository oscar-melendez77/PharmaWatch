variable "gcp_project_id" {
  description = "ID of the Google Cloud project that will hold everything"
  type        = string
}

variable "gcp_region" {
  description = "Region for the bucket and the BigQuery datasets, both must be in the same place"
  type        = string
  default     = "us-central1"
}
