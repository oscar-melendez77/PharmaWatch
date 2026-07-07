import os
import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone

import boto3
import requests
from airflow import DAG
from airflow.exceptions import AirflowException
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

OPENFDA_URL = "https://api.fda.gov/drug/label.json"
S3_BUCKET = "pharmawatch-data-lake"
SOURCE = "openfda"

ICEBERG_PACKAGES = (
    "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,"
    "software.amazon.awssdk:bundle:2.24.8,"
    "software.amazon.awssdk:url-connection-client:2.24.8"
)

default_args = {
    "owner": "pharmawatch",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def _s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ["AWS_REGION"],
    )


def _first_or_default(value, default):
    if isinstance(value, list) and value:
        return value[0]
    return default


def _first_text_clip(value, length=500):
    if isinstance(value, list) and value:
        text = value[0]
    elif isinstance(value, str):
        text = value
    else:
        return None
    if text is None:
        return None
    return text[:length]


def fetch_openfda(**context):
    params = {"limit": 100, "sort": "effective_time:desc"}
    response = requests.get(OPENFDA_URL, params=params, timeout=60)
    response.raise_for_status()
    results = response.json().get("results", []) or []
    context["ti"].xcom_push(key="raw_labels", value=results)


def filter_openfda(**context):
    raw = context["ti"].xcom_pull(key="raw_labels", task_ids="fetch_openfda") or []
    valid = []
    dropped = 0
    for record in raw:
        if not record.get("id"):
            dropped += 1
            continue
        if "openfda" not in record or record.get("openfda") is None:
            dropped += 1
            continue
        valid.append(record)
    logger.info("filter_openfda: kept=%d dropped=%d", len(valid), dropped)
    context["ti"].xcom_push(key="valid_labels", value=valid)


def load_openfda_to_s3(**context):
    valid = context["ti"].xcom_pull(key="valid_labels", task_ids="filter_openfda") or []
    ds = context["ds"]
    extracted = []
    for record in valid:
        openfda = record.get("openfda") or {}
        extracted.append({
            "drug_id": record.get("id"),
            "brand_name": _first_or_default(openfda.get("brand_name"), "unknown"),
            "generic_name": _first_or_default(openfda.get("generic_name"), "unknown"),
            "warnings": _first_text_clip(record.get("warnings"), 500),
            "interactions": _first_text_clip(record.get("drug_interactions"), 500),
            "ingestion_ts": datetime.now(timezone.utc).isoformat(),
        })

    body = "\n".join(json.dumps(r) for r in extracted).encode("utf-8")
    key = "raw/openfda/{}/openfda_{}.json".format(ds, ds)

    _s3_client().put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType="application/json")
    logger.info("load_openfda_to_s3: wrote %d records to s3://%s/%s", len(extracted), S3_BUCKET, key)


def validate_openfda(**context):
    ds = context["ds"]
    s3_key = "raw/{}/{}/{}_{}.json".format(SOURCE, ds, SOURCE, ds)
    tmp_path = "/tmp/{}_{}.json".format(SOURCE, ds)

    checkpoint_script = os.path.join(
        os.environ["PHARMAWATCH_ROOT"], "quality", "checkpoints", "run_checkpoint.py"
    )

    try:
        _s3_client().download_file(S3_BUCKET, s3_key, tmp_path)

        result = subprocess.run(
            ["python", checkpoint_script, SOURCE, tmp_path],
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.info(result.stdout)
        if result.stderr:
            logger.info(result.stderr)

        if result.returncode == 1:
            raise AirflowException("quality gate failed: {} ({})".format(SOURCE, ds))

        logger.info("quality gate passed: %s (%s)", SOURCE, ds)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as exc:
                logger.warning("Failed to remove temp file %s: %s", tmp_path, exc)


def ingest_to_iceberg(**context):
    ds = context["ds"]
    script = os.path.join(
        os.environ["PHARMAWATCH_ROOT"], "iceberg", "ingest_raw.py"
    )
    result = subprocess.run(
        ["spark-submit", "--packages", ICEBERG_PACKAGES, script, SOURCE, ds],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        logger.info(result.stdout)
    if result.stderr:
        logger.info(result.stderr)

    if result.returncode != 0:
        raise AirflowException("iceberg ingest failed: {} ({})".format(SOURCE, ds))

    logger.info("iceberg ingest done: %s (%s)", SOURCE, ds)


with DAG(
    dag_id="openfda_daily",
    default_args=default_args,
    start_date=datetime(2024, 1, 1),
    schedule_interval="0 8 * * *",
    catchup=False,
) as dag:
    fetch_task = PythonOperator(
        task_id="fetch_openfda",
        python_callable=fetch_openfda,
    )

    filter_task = PythonOperator(
        task_id="filter_openfda",
        python_callable=filter_openfda,
    )

    load_task = PythonOperator(
        task_id="load_openfda_to_s3",
        python_callable=load_openfda_to_s3,
    )

    validate_task = PythonOperator(
        task_id="validate_openfda",
        python_callable=validate_openfda,
    )

    ingest_task = PythonOperator(
        task_id="ingest_to_iceberg",
        python_callable=ingest_to_iceberg,
    )

    fetch_task >> filter_task >> load_task >> validate_task >> ingest_task
