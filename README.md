# PharmaWatch

[![CI](https://github.com/oscar-melendez77/PharmaWatch/actions/workflows/ci.yml/badge.svg)](https://github.com/oscar-melendez77/PharmaWatch/actions/workflows/ci.yml)
[![Live demo](https://img.shields.io/badge/demo-live-2a78d6)](https://oscar-melendez77.github.io/PharmaWatch/)

Drug risk intelligence platform. Give it a drug name + your age/weight and it spits out risk scores, side effects, a research digest, and a chat agent for follow-up questions.

**Live dashboard → https://oscar-melendez77.github.io/PharmaWatch/**

## How it works

You enter a drug and a basic profile. The system pulls adverse event data from FDA FAERS, papers from PubMed, drug labels from OpenFDA, and patient discussion from Reddit. Four LightGBM models score risk across serious reactions, hospitalization, death, and disability. SHAP explains what's driving the serious reaction score. A transparent, rule-based personalization layer then adjusts those drug-level scores for your age, sex, body weight, and habits (smoking, alcohol, polypharmacy, pregnancy) and shows exactly which factors moved the risk. A RAG agent (Groq + Llama 3) handles follow-up questions grounded in the research.

## Architecture

Terraform sets up AWS, GCP, and Snowflake. Reddit streams through Kafka into S3. FAERS, PubMed, and OpenFDA come in through Airflow DAGs. Great Expectations validates each batch before anything moves forward. Validated JSON then lands in Iceberg raw tables on S3 via Glue Catalog. Scala Spark jobs on Databricks do the heavy cleaning/enrichment into Iceberg clean tables. A loader reads those into Snowflake or BigQuery depending on trial status (auto-switches after 30 days). dbt builds the mart layer — the key table is `drug_master_profile` which joins everything per drug. LightGBM trains four classifiers tracked in MLflow and promotes the winners to Production. ChromaDB indexes PubMed abstracts, LangChain wires retrieval to Groq for the agent. FastAPI serves the backend from Render, Gradio frontend lives on HuggingFace Spaces, and a static dashboard is published to GitHub Pages.

```
raw APIs ──► Airflow / Kafka ──► S3 ──► GE gate ──► Iceberg (raw)
   ──► Spark clean (Iceberg) ──► warehouse (Snowflake/BigQuery)
   ──► dbt marts ──► LightGBM train+promote (MLflow) ──► ChromaDB index
   ──► FastAPI ──► Gradio UI + GitHub Pages dashboard
```

## Tech stack

| Layer | Tools |
|-------|-------|
| Infra | Terraform, AWS S3, IAM, Glue Catalog, GCP BigQuery, Snowflake |
| Ingestion | Confluent Kafka, Airflow, Reddit API, FDA FAERS, PubMed/NCBI, OpenFDA |
| Storage | Apache Iceberg on S3 |
| Quality | Great Expectations |
| Transform | Spark (Scala), dbt |
| Compute | Databricks |
| ML | LightGBM, MLflow, SHAP |
| RAG | ChromaDB, SentenceTransformers, LangChain, Groq/Llama 3 |
| Orchestration | Airflow (master DAG), portable Python runner, GitHub Actions |
| Serving | FastAPI (Render), Gradio (HuggingFace Spaces), GitHub Pages |

## Running it

The whole post-ingestion pipeline runs from one command (each stage shells out to
the same tool it uses in production, so a local run matches a scheduled one):

```bash
python orchestration/pipeline.py                 # full chain, ds=today (UTC)
python orchestration/pipeline.py --from dbt_build # resume partway
python orchestration/pipeline.py --list           # show stages
```

Stages: `ingest_raw → spark transform → warehouse load → dbt build → ml train+promote → rag embed`.
On a schedule this runs as the `master_pipeline` Airflow DAG (which also triggers
the three ingestion DAGs first).

Publish the static dashboard:

```bash
python serving/export_static.py                   # writes docs/data/dashboard.json
python -m http.server --directory docs            # preview at localhost:8000
```

`export_static.py` pulls the live `/predict` API when `API_BASE_URL` is set,
otherwise it generates sample fixtures so the dashboard renders with zero infra.

Tests:

```bash
pip install pytest pandas numpy && pytest -q
```

## Automation

- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) — byte-compiles the codebase, lints, validates the dashboard data, and runs the pytest suite on every push/PR.
- **Daily refresh** ([`.github/workflows/daily_pipeline.yml`](.github/workflows/daily_pipeline.yml)) — regenerates `docs/data/` and commits it back so the GitHub Pages dashboard stays current without always-on infra.

## Project layout

```
terraform/       infra-as-code (AWS, GCP, Snowflake)
iceberg/         lake table setup + S3-JSON -> raw Iceberg ingestion
kafka/           reddit streaming (producer + consumer)
airflow/         batch ingestion DAGs + master pipeline DAG
quality/         great expectations suites
spark/           scala transformers (Databricks)
warehouse/       snowflake + bigquery loaders + clean-Iceberg load runner
dbt/             sql transforms, mart layer
ml/              lightgbm training + promotion, prediction, shap
rag/             chromadb + langchain + groq agent
api/             fastapi backend
ui/              gradio frontend
orchestration/   single-entrypoint pipeline runner
serving/         static export for the Pages dashboard
docs/            GitHub Pages dashboard (index.html + data/)
tests/           pytest suite
.github/         CI + daily refresh workflows
```

## Why these choices

**Python + Scala + SQL + HCL** — each where it makes sense. Scala for Spark because type safety catches schema issues at compile time. Python for ML/API/orchestration. SQL for dbt.

**Kafka for Reddit, Airflow for the rest** — Reddit is continuous and bursty. FAERS/PubMed/OpenFDA update daily at best, so batch is fine.

**Switchable warehouse** — Snowflake trial expires after 30 days. Set `WAREHOUSE=auto` and it falls back to BigQuery. No code changes needed.

**Two quality layers** — Airflow filters bad records before writing to S3, then a separate GE checkpoint downloads the file and runs the full suite. If the checkpoint fails, the DAG stops.

**SHAP on serious model only** — it's expensive. The serious reaction score is what users look at first. The other three models just report probabilities.

**Static dashboard for the demo** — the Pages site is pre-computed and infra-free, so the project stays demoable without paying to keep Databricks/Snowflake/Render running.

## Demo & deployment

- **Dashboard (live):** https://oscar-melendez77.github.io/PharmaWatch/ — GitHub Pages, source in `docs/`.
- **API:** deploy to Render with [`api/render.yaml`](api/render.yaml) (set the secrets in the dashboard); docs at `/docs`.
- **UI:** deploy `ui/` to HuggingFace Spaces (config in [`ui/README.md`](ui/README.md)); set `API_BASE_URL` to the Render URL.

Full step-by-step deploy instructions are in [DEPLOY.md](DEPLOY.md). See
[ROADMAP.md](ROADMAP.md) for build status and remaining work.

## Author

Oscar Melendez — [github.com/oscar-melendez77](https://github.com/oscar-melendez77)
