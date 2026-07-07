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

FDA_URL = "https://api.fda.gov/drug/event.json"
S3_BUCKET = "pharmawatch-data-lake"
SOURCE = "faers"

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


def fetch_faers(**context):
    params = {"limit": 1000, "sort": "receivedate:desc"}
    response = requests.get(FDA_URL, params=params, timeout=60)
    response.raise_for_status()
    results = response.json().get("results", []) or []
    context["ti"].xcom_push(key="raw_records", value=results)


def _flag(value):
    try:
        return 1 if int(value) == 1 else 0
    except (TypeError, ValueError):
        return 0


def _extract(record):
    patient = record.get("patient") or {}
    drugs = patient.get("drug") or []
    reactions = patient.get("reaction") or []
    drug_name = drugs[0].get("medicinalproduct") if drugs else None
    reaction = reactions[0].get("reactionmeddrapt") if reactions else None

    age = patient.get("patientonsetage")
    try:
        age_val = int(age) if age is not None else None
    except (TypeError, ValueError):
        age_val = None

    weight = patient.get("patientweight")
    try:
        weight_val = float(weight) if weight is not None else None
    except (TypeError, ValueError):
        weight_val = None

    return {
        "report_id": record.get("safetyreportid"),
        "drug_name": drug_name,
        "reaction": reaction,
        "severity": record.get("serious"),
        "age": age_val,
        "weight": weight_val,
        "report_date": record.get("receivedate"),
        "hospitalization": _flag(record.get("seriousnesshospitalization")),
        "death": _flag(record.get("seriousnessdeath")),
        "disability": _flag(record.get("seriousnessdisabling")),
        "ingestion_ts": datetime.now(timezone.utc).isoformat(),
    }


def filter_faers(**context):
    raw = context["ti"].xcom_pull(key="raw_records", task_ids="fetch_faers") or []
    valid = []
    dropped = 0
    for record in raw:
        extracted = _extract(record)
        if not extracted.get("report_id"):
            dropped += 1
            continue
        if not extracted.get("drug_name"):
            dropped += 1
            continue
        if extracted.get("severity") not in (1, 2, "1", "2"):
            dropped += 1
            continue
        valid.append(extracted)
    logger.info("filter_faers: kept=%d dropped=%d", len(valid), dropped)
    context["ti"].xcom_push(key="valid_records", value=valid)


def load_faers_to_s3(**context):
    valid = context["ti"].xcom_pull(key="valid_records", task_ids="filter_faers") or []
    ds = context["ds"]

    body = "\n".join(json.dumps(r) for r in valid).encode("utf-8")
    key = "raw/faers/{}/faers_{}.json".format(ds, ds)

    _s3_client().put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType="application/json")
    logger.info("load_faers_to_s3: wrote %d records to s3://%s/%s", len(valid), S3_BUCKET, key)


def validate_faers(**context):
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
    dag_id="faers_daily",
    default_args=default_args,
    start_date=datetime(2024, 1, 1),
    schedule_interval="0 6 * * *",
    catchup=False,
) as dag:
    fetch_task = PythonOperator(
        task_id="fetch_faers",
        python_callable=fetch_faers,
    )

    filter_task = PythonOperator(
        task_id="filter_faers",
        python_callable=filter_faers,
    )

    load_task = PythonOperator(
        task_id="load_faers_to_s3",
        python_callable=load_faers_to_s3,
    )

    validate_task = PythonOperator(
        task_id="validate_faers",
        python_callable=validate_faers,
    )

    ingest_task = PythonOperator(
        task_id="ingest_to_iceberg",
        python_callable=ingest_to_iceberg,
    )

    fetch_task >> filter_task >> load_task >> validate_task >> ingest_task
