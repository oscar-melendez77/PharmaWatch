# PharmaWatch — Completion Roadmap

Status as of 2026-07-07. Every component in the pipeline exists as real code, but the
project is **not yet wired end-to-end**. Several stages read from inputs that nothing
upstream produces. This roadmap closes those gaps in dependency order so the pipeline
can run raw → clean → warehouse → mart → model → index → serve.

## Build progress

| Phase | Work | Status |
|-------|------|--------|
| 1 | S3 raw JSON → raw Iceberg loader + DAG wiring | ✅ done |
| 2 | Clean Iceberg → warehouse runner + idempotent loaders + schema fix | ✅ done |
| 3 | MLflow AUC-gated model promotion | ✅ done |
| 4 | Single orchestration entrypoint + master DAG | ✅ done |
| 5 | Static export + live GitHub Pages dashboard | ✅ done (live) |
| 6 | GitHub Actions daily refresh + CI | ✅ done (running) |
| 7 | pytest suite, gating CI | ✅ done (green) |
| 8 | Deployment configs + README refresh | ✅ done |
| 9 | Personalized risk profile (gender/habits) | planned |

The four blocking seams below (Stages 1–2 of the original plan) are closed. What
remains needs live cloud credentials (deploy the API/UI, provision infra) or is the
Phase 9 personalization enhancement.

## Current state

| Layer | Built | Runnable in isolation | Wired to neighbors |
|-------|:-----:|:---------------------:|:------------------:|
| Terraform (S3, IAM, BQ, Snowflake) | ✅ | ✅ | partial |
| Iceberg table DDL | ✅ | ✅ | — |
| Kafka (Reddit → S3) | ✅ | ✅ | ✅ |
| Airflow DAGs (faers/pubmed/openfda → S3) | ✅ | ✅ | ends at S3 |
| Great Expectations gate | ✅ | ✅ | ✅ (in DAGs) |
| Spark transformers (raw→clean Iceberg) | ✅ | ✅ | ❌ input empty |
| Warehouse loaders (snowflake/bq/auto) | ✅ | ✅ | ❌ no caller |
| dbt marts | ✅ | ✅ | depends on warehouse |
| ML (train/predict + SHAP) | ✅ | ✅ | ❌ no Production stage |
| RAG (embed/retrieve/agent) | ✅ | ✅ | depends on warehouse |
| FastAPI + Gradio | ✅ | ✅ | depends on models |

### The four blocking seams
1. **S3 raw JSON → raw Iceberg** — DAGs stop after writing JSON to S3; Spark reads
   `glue_catalog.pharmawatch.*` raw tables that nothing populates.
2. **Clean Iceberg → warehouse** — `warehouse/loader.load_all()` has zero callers; no
   code reads the clean Iceberg tables and hands them to the loader.
3. **MLflow model promotion** — `train.py` registers models but never transitions them to
   the `Production` stage; `predict.py` loads `models:/…/Production` and 404s until then.
4. **No top-level orchestration** — nothing triggers Spark → warehouse → dbt → train →
   embed after ingestion.

---

## Stage 0 — Provisioning parity (Terraform)

Bring IaC in line with what the code assumes exists.

- Add `aws_glue_catalog_database` for `pharmawatch` and `pharmawatch_clean` (currently
  created ad-hoc by Spark).
- Add Databricks workspace + job cluster resources (or document the manual Databricks
  setup if staying on the free tier).
- Add the Confluent Kafka topic `reddit.drug_mentions` (Terraform `confluent` provider or
  a documented manual step).
- Add `SNOWFLAKE_TRIAL_START` and warehouse role/grants needed by the loaders.

**Acceptance:** `terraform apply` from clean produces every resource the code references;
no ad-hoc console setup required.

---

## Stage 1 — Land raw data in Iceberg (closes seam #1)

The DAGs already produce validated newline-JSON in `s3://pharmawatch-data-lake/raw/<source>/…`.
Add the missing hop into the raw Iceberg tables.

- New Spark job `spark/…/RawIngestion.scala` (or a PySpark `iceberg/ingest_raw.py`) that,
  per source, reads the day's S3 JSON and `MERGE INTO` / appends to
  `glue_catalog.pharmawatch.{faers_events,pubmed_articles,reddit_mentions,drug_labels}`.
- Add a final `ingest_to_iceberg` task to each Airflow DAG (faers/pubmed/openfda) that
  triggers this job for the run's partition date.
- Reddit path: add the same S3-JSON → Iceberg step for `raw/reddit/…` (Kafka consumer
  already lands the files).

**Acceptance:** after a DAG run, `SELECT count(*)` on each raw Iceberg table is non-zero
and matches the validated record count.

---

## Stage 2 — Clean transforms + warehouse load (closes seam #2)

Spark clean transformers already exist; add the load hop and run order.

- New runner `warehouse/run_load.py` that reads the four **clean** Iceberg tables
  (`glue_catalog.pharmawatch_clean.*`) into DataFrames and calls
  `warehouse.loader.load(faers_df, pubmed_df, reddit_df, labels_df)`.
- Make loaders **idempotent** — truncate/replace or `MERGE` instead of blind `INSERT`
  (current `snowflake_loader` appends, which double-loads on re-run).
- Confirm `WAREHOUSE=auto` switch works against a real Snowflake trial + BigQuery fallback.

**Acceptance:** `TransformerRunner` then `run_load.py` populates warehouse tables that
match clean Iceberg row counts; re-running does not duplicate rows.

---

## Stage 3 — dbt marts

Mostly built; verify against real warehouse data.

- Run `dbt build` against the loaded warehouse; fix any type/join mismatches in
  `drug_master_profile` (esp. the `brand_name = drug_name` join key).
- Expand tests beyond `assert_risk_score_range`: not-null/unique on `drug_name`,
  referential checks between marts, freshness on sources.

**Acceptance:** `dbt build` passes green; `drug_master_profile` has one row per drug with
adverse + sentiment + research + label columns populated.

---

## Stage 4 — ML training + promotion (closes seam #3)

- Add MLflow model promotion: after training, use `MlflowClient` to transition the newest
  version of each of the 4 models to `Production` (gated on a min AUC threshold).
- Persist the SHAP background/expected value alongside the model so `predict.py` doesn't
  rebuild the explainer cold each request (or cache the `TreeExplainer`).
- Add a champion/challenger guard: only promote if new AUC ≥ current Production AUC.

**Acceptance:** `predict.py` loads all four `…/Production` models with no "missing
production model" fallback; `/predict` returns real probabilities + SHAP.

---

## Stage 5 — RAG index

- Run `rag/embed.py` against the loaded `pubmed_articles`; confirm ChromaDB persists to
  `CHROMA_PERSIST_DIR` and `retriever.retrieve` returns hits.
- Decide ChromaDB persistence for the deployed API (bundled volume vs. rebuilt on boot vs.
  hosted). Document it.

**Acceptance:** `/ask` and `/digest` return grounded answers citing real PubMed IDs.

---

## Stage 6 — End-to-end orchestration (closes seam #4)

Wire the stages into one schedulable pipeline.

- Master Airflow DAG (or extend existing) chaining:
  `ingest (S3) → GE gate → S3→raw Iceberg → Spark clean → warehouse load → dbt build →
  ml train+promote → rag embed`.
- Use Databricks operator / `spark-submit` for the Scala jobs; `BashOperator`/`dbt` for
  the SQL and Python steps.
- Add sensors so downstream steps wait on upstream partitions; fail closed on the GE gate.

**Acceptance:** a single trigger runs the whole chain and ends with fresh models +
refreshed ChromaDB, with the GE gate able to halt the run.

---

## Stage 7 — Deployment

- Deploy FastAPI to Render via `api/render.yaml`; set all env vars; confirm `/health`
  reports the active warehouse.
- Deploy Gradio to HuggingFace Spaces (`ui/`); point `API_BASE_URL` at the Render URL.
- Replace the placeholder demo URLs in `README.md` with the live links.

**Acceptance:** public UI → API → models round-trips for a real drug (e.g. Adderall).

---

## Stage 8 — Testing, CI, hardening

- Unit tests: `ml/features`, `predict` KPI math, DAG `_extract`/`filter`, loaders (mocked).
- GitHub Actions: lint (ruff/black, scalafmt), `dbt build` against a CI warehouse, pytest,
  `terraform validate`.
- Secrets hygiene: confirm `.env` never committed; document required env per component.
- Observability: structured logs + basic run metrics on the master DAG.

**Acceptance:** CI green on PRs; a new contributor can run each stage from the README.

---

## Suggested order & effort

| Stage | Blocks | Rough effort |
|-------|--------|--------------|
| 1 — S3 → raw Iceberg | everything downstream | M |
| 2 — clean → warehouse | dbt, ML, RAG | M |
| 4 — ML promotion | `/predict`, `/ask` | S |
| 3 — dbt verify | ML features | S |
| 5 — RAG index | `/ask`, `/digest` | S |
| 6 — orchestration | reproducible runs | L |
| 0 — Terraform parity | clean re-provision | M |
| 7 — deployment | public demo | M |
| 8 — testing/CI | maintainability | M |

Stages 1 → 2 → 3/4 → 5 are the critical path to a working end-to-end run. Stage 0 can be
done in parallel; 6–8 harden and ship it.
