import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
from google.cloud import bigquery, storage

# 1. config
load_dotenv()
PROJECT_ID = os.environ["GCP_PROJECT_ID"]
BUCKET_NAME = os.environ["GCS_BUCKET"]
DATASET = os.environ["BQ_DATASET_RAW"]
LOCATION = os.environ["BQ_LOCATION"]

RAW_DIR = Path("data/raw")
PARQUET_DIR = Path("data/parquet")

SOURCES = [
    {
        "name": "faers",
        "table": "faers",
        "schema": pa.schema([
            ("report_id", pa.string()),
            ("drug_name", pa.string()),
            ("reported_drug", pa.string()),
            ("reaction", pa.string()),
            ("severity", pa.string()),
            ("age_years", pa.float64()),
            ("age_unit", pa.string()),
            ("weight", pa.float64()),
            ("report_date", pa.string()),
            ("hospitalization", pa.int64()),
            ("death", pa.int64()),
            ("disability", pa.int64()),
            ("ingestion_ts", pa.string()),
        ]),
    },
    {
        "name": "openfda",
        "table": "openfda_labels",
        "schema": pa.schema([
            ("drug_id", pa.string()),
            ("drug_name", pa.string()),
            ("brand_name", pa.string()),
            ("generic_name", pa.string()),
            ("warnings", pa.string()),
            ("interactions", pa.string()),
            ("ingestion_ts", pa.string()),
        ]),
    },
    {
        "name": "pubmed",
        "table": "pubmed_articles",
        "schema": pa.schema([
            ("article_id", pa.string()),
            ("title", pa.string()),
            ("abstract", pa.string()),
            ("publish_date", pa.string()),
            ("drug_name", pa.string()),
            ("ingestion_ts", pa.string()),
        ]),
    },
    {
        "name": "clinicaltrials",
        "table": "clinicaltrials_studies",
        "schema": pa.schema([
            ("nct_id", pa.string()),
            ("title", pa.string()),
            ("drug", pa.string()),
            ("status", pa.string()),
            ("phase", pa.string()),
            ("enrollment", pa.int64()),
            ("start_date", pa.string()),
            ("adverse_events", pa.list_(pa.struct([
                ("term", pa.string()),
                ("is_serious", pa.bool_()),
                ("num_affected", pa.int64()),
                ("num_at_risk", pa.int64()),
            ]))),
            ("ingestion_ts", pa.string()),
        ]),
    },
]

storage_client = storage.Client(project=PROJECT_ID)
bq_client = bigquery.Client(project=PROJECT_ID, location=LOCATION)
bucket = storage_client.bucket(BUCKET_NAME)

# 2. convert the latest json file of each source to parquet
staged = {}
for source in SOURCES:
    name = source["name"]
    latest_file = sorted(RAW_DIR.glob(name + "_*.json"))[-1]
    ingest_date = latest_file.stem.split("_")[-1]

    with open(latest_file, encoding="utf-8") as f:
        records = json.load(f)

    table = pa.Table.from_pylist(records, schema=source["schema"])

    parquet_path = PARQUET_DIR / name / ("ingest_date=" + ingest_date) / (name + ".parquet")
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, parquet_path)

    staged[name] = {
        "parquet_path": parquet_path,
        "blob_path": "raw/" + name + "/ingest_date=" + ingest_date + "/" + name + ".parquet",
        "rows": len(records),
    }
    print("[2/5] " + name + ": " + str(len(records)) + " records -> " + str(parquet_path))

# 3. upload the parquet files to the bucket
for source in SOURCES:
    name = source["name"]
    blob = bucket.blob(staged[name]["blob_path"])
    blob.upload_from_filename(str(staged[name]["parquet_path"]))
    print("[3/5] uploaded gs://" + BUCKET_NAME + "/" + staged[name]["blob_path"])

# 4. load each parquet file from the bucket into its BigQuery table
for source in SOURCES:
    name = source["name"]
    uri = "gs://" + BUCKET_NAME + "/" + staged[name]["blob_path"]
    table_id = PROJECT_ID + "." + DATASET + "." + source["table"]

    parquet_options = bigquery.ParquetOptions()
    parquet_options.enable_list_inference = True

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        parquet_options=parquet_options,
    )

    job = bq_client.load_table_from_uri(uri, table_id, job_config=job_config)
    job.result()
    print("[4/5] loaded " + table_id)

# 5. verify row counts and column types in BigQuery
for source in SOURCES:
    name = source["name"]
    table_id = PROJECT_ID + "." + DATASET + "." + source["table"]
    bq_table = bq_client.get_table(table_id)

    if bq_table.num_rows == staged[name]["rows"]:
        status = "[OK]"
    else:
        status = "[MISMATCH]"
    print("[5/5] " + status + " " + source["table"] + ": file " + str(staged[name]["rows"]) + ", bigquery " + str(bq_table.num_rows))

    for field in bq_table.schema:
        print("        " + field.name + " " + field.field_type + " " + field.mode)
