# 1. turn on the Google APIs the keyless login needs

resource "google_project_service" "iamcredentials" {
  project            = var.gcp_project_id
  service            = "iamcredentials.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "sts" {
  project            = var.gcp_project_id
  service            = "sts.googleapis.com"
  disable_on_destroy = false
}

# 2. the pool that lets GitHub Actions prove who it is, without any password or key file

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.gcp_project_id
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "Lets workflows of one GitHub repository act as service accounts"

  depends_on = [
    google_project_service.iam,
    google_project_service.iamcredentials,
    google_project_service.sts,
  ]
}

# only workflows that run in this repository are accepted
resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.gcp_project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

locals {
  github_principal = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

# 3. a second service account that can only deploy the dashboard

resource "google_service_account" "deployer" {
  account_id   = "pharmawatch-deployer"
  display_name = "PharmaWatch deployer"
  description  = "Used by GitHub Actions to push the dashboard image and deploy it to Cloud Run"

  depends_on = [google_project_service.iam]
}

# 4. the repository may act as the pipeline account and as the deployer account

resource "google_service_account_iam_member" "github_as_pipeline" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.github_principal
}

resource "google_service_account_iam_member" "github_as_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.github_principal
}

# 5. where the dashboard images are stored, only the newest few are kept

resource "google_artifact_registry_repository" "images" {
  project       = var.gcp_project_id
  location      = var.gcp_region
  repository_id = "pharmawatch"
  format        = "DOCKER"
  description   = "Dashboard images built by the daily workflow"

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "604800s"
    }
  }

  depends_on = [google_project_service.artifactregistry]
}

# 6. what the deployer is allowed to do

resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  project    = var.gcp_project_id
  location   = var.gcp_region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_run" {
  project = var.gcp_project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# Cloud Run only lets an account deploy a service that runs as another account if it may act as that account
resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = "projects/${var.gcp_project_id}/serviceAccounts/${var.cloud_run_runtime_service_account}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}
