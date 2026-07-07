# PharmaWatch — Deployment Runbook

Everything in the codebase is wired and tested (see [ROADMAP.md](ROADMAP.md)). What's
left is operational: provision the cloud, run the pipeline once, deploy the two
services, and flip the demo to live data. This runbook is the ordered, copy-paste
path. Budget ~2–3 hours the first time.

> All accounts here have free tiers: AWS (free-tier S3/IAM), GCP BigQuery sandbox,
> Snowflake 30-day trial, Render free web service, HuggingFace Spaces, Groq API,
> and a hosted MLflow (or self-host). The `WAREHOUSE=auto` switch falls back from
> Snowflake to BigQuery when the trial expires.

## Prerequisites

| Tool | Used for |
|------|----------|
| `terraform` ≥ 1.5 | provision S3/IAM/BigQuery/Snowflake |
| `aws` CLI | S3 access, credentials |
| `gcloud` CLI | BigQuery auth (`gcloud auth application-default login`) |
| Python 3.11 + `pip` | pipeline, ML, API |
| Java 11 + Spark 3.5 (or Databricks) | Iceberg jobs (`spark-submit`) |
| `sbt` | build/run the Scala transformers |
| `dbt-core` + `dbt-snowflake` / `dbt-bigquery` | mart layer |
| `gh` CLI | set GitHub Actions secrets |
| Accounts | AWS, GCP, Snowflake, Confluent, Reddit, Groq, MLflow, Render, HuggingFace |

## Secrets — where each one goes

Fill a local `.env` from [`.env.example`](.env.example) for the pipeline. The same
values are re-entered in three hosting surfaces later:

| Secret group | `.env` (pipeline) | Render (API) | HF Space (UI) | GH Actions (daily) |
|---|:---:|:---:|:---:|:---:|
| AWS_* | ✓ | | | |
| SNOWFLAKE_* / GCP_* / WAREHOUSE | ✓ | ✓ | | |
| MLFLOW_TRACKING_URI | ✓ | ✓ | | |
| GROQ_API_KEY | ✓ | ✓ | | |
| CHROMA_PERSIST_DIR | ✓ | ✓ | | |
| CONFLUENT_* / REDDIT_* | ✓ | | | |
| API_BASE_URL | | | ✓ | ✓ (optional) |

```bash
cp .env.example .env
# edit .env with real values, then:
set -a && source .env && set +a
export PHARMAWATCH_ROOT="$(pwd)"
```

---

## Step 1 — Provision infrastructure

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars     # fill in aws_region, gcp_project_id,
                                                 # snowflake_account/username/password
terraform init
terraform plan
terraform apply
cd ..
```

This creates the S3 data lake (versioned, locked-down), the Spark IAM role, the
BigQuery dataset, and the Snowflake warehouse.

**Not covered by Terraform** (do these manually — see ROADMAP Stage 0):
- **Confluent** Kafka topic `reddit.drug_mentions` (create in the Confluent Cloud UI).
- **Databricks** workspace/cluster, if you run Spark there instead of locally.
- The **Glue Catalog databases** are created by the Spark jobs in Step 2, not Terraform.

**Verify:** `aws s3 ls s3://pharmawatch-data-lake` succeeds (empty is fine).

---

## Step 2 — First pipeline run (fill the warehouse + train models)

### 2a. Create the Iceberg tables

```bash
spark-submit --packages "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,software.amazon.awssdk:bundle:2.24.8,software.amazon.awssdk:url-connection-client:2.24.8" \
  iceberg/setup_iceberg.py
```

### 2b. Ingest raw data to S3

Bring up Airflow and run the three ingestion DAGs (they fetch → filter → write S3 →
GE gate → land raw Iceberg), plus start the Kafka consumer for Reddit:

```bash
export AIRFLOW_HOME="$PHARMAWATCH_ROOT/airflow"
airflow db init
airflow dags trigger faers_daily
airflow dags trigger pubmed_daily
airflow dags trigger openfda_daily
# Reddit stream (separate terminal, runs continuously):
python kafka/producer.py &     # publishes drug mentions
python kafka/consumer.py       # batches to S3; then land it:
spark-submit iceberg/ingest_raw.py reddit "$(date -u +%F)"
```

> No Airflow? You can drive the same functions manually, but the DAGs are the
> supported path. On a schedule, the `master_pipeline` DAG does all of Step 2 nightly.

### 2c. Run the downstream chain

Once raw Iceberg is populated, one command runs the rest:

```bash
python orchestration/pipeline.py --from transform
# transform -> warehouse load -> dbt build -> ml train+promote -> rag embed
```

Make sure `dbt/pharmawatch/profiles.yml` points at your warehouse, and MLflow is
reachable (`MLFLOW_TRACKING_URI`).

**Verify:**
- Warehouse: `select count(*) from drug_master_profile;` is non-zero.
- MLflow: each `pharmawatch-{serious,hospitalization,death,disability}` model has a
  version in the **Production** stage.
- ChromaDB: the persist dir exists and has vectors.

---

## Step 3 — Deploy the services

### 3a. API → Render

1. New → **Blueprint**, point Render at this repo; it reads [`api/render.yaml`](api/render.yaml).
2. In the service **Environment**, set every `set_in_dashboard` value: `WAREHOUSE`,
   all `SNOWFLAKE_*`, `GCP_PROJECT_ID`, `BIGQUERY_DATASET`, `MLFLOW_TRACKING_URI`,
   `GROQ_API_KEY`, `CHROMA_PERSIST_DIR`, `PHARMAWATCH_ROOT`.
3. Deploy. **Verify:** `curl https://<your-api>.onrender.com/health` returns
   `{"status":"ok","warehouse":"..."}` and `/docs` loads.

### 3b. UI → HuggingFace Spaces

1. Create a **Gradio** Space; push the contents of `ui/` to it (the frontmatter in
   [`ui/README.md`](ui/README.md) configures the SDK and `app_file`).
2. Space **Settings → Secrets**: set `API_BASE_URL` to the Render URL.
3. **Verify:** open the Space, analyze "Adderall" with age/sex/habits — risk scores,
   SHAP, and the personalized panel populate.

---

## Step 4 — Flip the demo to live data

The GitHub Pages dashboard currently shows sample fixtures. Point it at the live API:

```bash
# one-off, locally:
API_BASE_URL="https://<your-api>.onrender.com" python serving/export_static.py
git add docs/data/dashboard.json && git commit -m "chore: live dashboard data" && git push
```

To keep it live automatically, give the daily workflow the API URL:

```bash
gh secret set API_BASE_URL --repo oscar-melendez77/PharmaWatch --body "https://<your-api>.onrender.com"
```

Now `.github/workflows/daily_pipeline.yml` regenerates `docs/data/` from the live API
each day and commits it — the badge on the dashboard flips from "sample data" to
"live data".

**Enable Pages** (once): repo **Settings → Pages → Source: Deploy from branch →
`main` / `/docs`**. Live at https://oscar-melendez77.github.io/PharmaWatch/.

---

## Post-deploy checklist

- [ ] `terraform apply` clean; S3 bucket exists
- [ ] Raw Iceberg tables non-empty
- [ ] `drug_master_profile` populated in the warehouse
- [ ] 4 models in MLflow **Production**
- [ ] `/health` green on Render
- [ ] HF Space predicts + personalizes
- [ ] Pages badge shows "live data"
- [ ] Daily workflow run succeeds (Actions tab)

## Teardown

```bash
cd terraform && terraform destroy    # removes S3/IAM/BigQuery/Snowflake
```

Also delete the Render service, the HF Space, and the `reddit.drug_mentions`
Confluent topic. Remove the `API_BASE_URL` GitHub secret to return the dashboard to
sample mode.
