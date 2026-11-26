terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}

# 1. turn on the Google APIs this project uses

resource "google_project_service" "bigquery" {
  project            = var.gcp_project_id
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "storage" {
  project            = var.gcp_project_id
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "iam" {
  project            = var.gcp_project_id
  service            = "iam.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "run" {
  project            = var.gcp_project_id
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbuild" {
  project            = var.gcp_project_id
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifactregistry" {
  project            = var.gcp_project_id
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

# 2. the data lake bucket, raw files from the extract scripts land here

resource "google_storage_bucket" "raw" {
  name                        = "${var.gcp_project_id}-pharmawatch-raw"
  location                    = var.gcp_region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  labels = {
    project = "pharmawatch"
  }

  depends_on = [google_project_service.storage]
}

# 3. the warehouse, one dataset per layer

resource "google_bigquery_dataset" "raw" {
  dataset_id                 = "pharmawatch_raw"
  friendly_name              = "PharmaWatch raw"
  description                = "Tables loaded as-is from the files in the raw bucket"
  location                   = var.gcp_region
  delete_contents_on_destroy = false

  labels = {
    project = "pharmawatch"
    layer   = "raw"
  }

  depends_on = [google_project_service.bigquery]
}

resource "google_bigquery_dataset" "staging" {
  dataset_id                 = "pharmawatch_staging"
  friendly_name              = "PharmaWatch staging"
  description                = "Cleaned and typed tables built by dbt"
  location                   = var.gcp_region
  delete_contents_on_destroy = false

  labels = {
    project = "pharmawatch"
    layer   = "staging"
  }

  depends_on = [google_project_service.bigquery]
}

resource "google_bigquery_dataset" "marts" {
  dataset_id                 = "pharmawatch_marts"
  friendly_name              = "PharmaWatch marts"
  description                = "Final tables the signal detection and the dashboard read"
  location                   = var.gcp_region
  delete_contents_on_destroy = false

  labels = {
    project = "pharmawatch"
    layer   = "marts"
  }

  depends_on = [google_project_service.bigquery]
}

# 4. the service account the pipeline runs as

resource "google_service_account" "pipeline" {
  account_id   = "pharmawatch-pipeline"
  display_name = "PharmaWatch pipeline"
  description  = "Used by the scheduled extract, load and dbt jobs"

  depends_on = [google_project_service.iam]
}

# 5. what the service account is allowed to do, and nothing more

# read and write files in the raw bucket only
resource "google_storage_bucket_iam_member" "pipeline_bucket" {
  bucket = google_storage_bucket.raw.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

# create and edit tables in the three datasets only
resource "google_bigquery_dataset_iam_member" "pipeline_raw" {
  dataset_id = google_bigquery_dataset.raw.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_bigquery_dataset_iam_member" "pipeline_staging" {
  dataset_id = google_bigquery_dataset.staging.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_bigquery_dataset_iam_member" "pipeline_marts" {
  dataset_id = google_bigquery_dataset.marts.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.pipeline.email}"
}

# run query and load jobs, BigQuery needs this at project level
resource "google_project_iam_member" "pipeline_jobs" {
  project = var.gcp_project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}
