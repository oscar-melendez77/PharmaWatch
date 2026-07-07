import os
import json
import logging
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import boto3
import requests
from airflow import DAG
from airflow.exceptions import AirflowException
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
QUERY = "drug adverse effects OR drug toxicity OR pharmacovigilance"
S3_BUCKET = "pharmawatch-data-lake"
SOURCE = "pubmed"

ICEBERG_PACKAGES = (
    "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,"
    "software.amazon.awssdk:bundle:2.24.8,"
    "software.amazon.awssdk:url-connection-client:2.24.8"
)

DRUG_KEYWORDS = [
    "Adderall", "Oxycodone", "Xanax", "Metformin", "Lisinopril",
    "Atorvastatin", "Ibuprofen", "Amoxicillin", "Methadone", "Fentanyl",
]

MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}

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


def _normalize_month(value):
    if not value:
        return "01"
    if value in MONTH_MAP:
        return MONTH_MAP[value]
    if value.isdigit():
        return value.zfill(2)
    return "01"


def _parse_pub_date(pub_date_el):
    if pub_date_el is None:
        return None
    year_el = pub_date_el.find("Year")
    month_el = pub_date_el.find("Month")
    year = year_el.text if year_el is not None and year_el.text else None
    month = month_el.text if month_el is not None and month_el.text else None
    if not year:
        medline_el = pub_date_el.find("MedlineDate")
        if medline_el is not None and medline_el.text:
            token = medline_el.text.strip().split(" ")[0]
            if token.isdigit():
                year = token
    if not year:
        return None
    month_norm = _normalize_month(month)
    return "{}-{}-01".format(year, month_norm)


def _match_drug(title):
    if not title:
        return "unknown"
    lowered = title.lower()
    for drug in DRUG_KEYWORDS:
        if drug.lower() in lowered:
            return drug
    return "unknown"


def fetch_pubmed(**context):
    esearch_params = {
        "db": "pubmed",
        "term": QUERY,
        "retmax": 200,
        "sort": "pub+date",
        "retmode": "json",
    }
    esearch_resp = requests.get(ESEARCH_URL, params=esearch_params, timeout=60)
    esearch_resp.raise_for_status()
    pmids = esearch_resp.json().get("esearchresult", {}).get("idlist", []) or []

    articles = []
    if pmids:
        efetch_params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "rettype": "xml",
            "retmode": "xml",
        }
        efetch_resp = requests.get(EFETCH_URL, params=efetch_params, timeout=60)
        efetch_resp.raise_for_status()
        root = ET.fromstring(efetch_resp.content)

        for article_el in root.findall(".//PubmedArticle"):
            pmid_el = article_el.find(".//MedlineCitation/PMID")
            title_el = article_el.find(".//Article/ArticleTitle")
            abstract_parts = [
                (ae.text or "") for ae in article_el.findall(".//Article/Abstract/AbstractText")
            ]
            abstract_text = " ".join(p for p in abstract_parts if p).strip() or None
            pub_date_el = article_el.find(".//Article/Journal/JournalIssue/PubDate")
            publish_date = _parse_pub_date(pub_date_el)

            title_text = title_el.text if title_el is not None else None

            articles.append({
                "article_id": pmid_el.text if pmid_el is not None else None,
                "title": title_text,
                "abstract": abstract_text,
                "publish_date": publish_date,
                "drug_name": _match_drug(title_text),
            })

    context["ti"].xcom_push(key="raw_articles", value=articles)


def filter_pubmed(**context):
    raw = context["ti"].xcom_pull(key="raw_articles", task_ids="fetch_pubmed") or []
    valid = []
    dropped = 0
    for record in raw:
        if not record.get("article_id"):
            dropped += 1
            continue
        if not record.get("title"):
            dropped += 1
            continue
        abstract = record.get("abstract")
        if abstract is None or abstract == "":
            dropped += 1
            continue
        valid.append(record)
    logger.info("filter_pubmed: kept=%d dropped=%d", len(valid), dropped)
    context["ti"].xcom_push(key="valid_articles", value=valid)


def load_pubmed_to_s3(**context):
    valid = context["ti"].xcom_pull(key="valid_articles", task_ids="filter_pubmed") or []
    ds = context["ds"]
    enriched = []
    for record in valid:
        record = dict(record)
        record["ingestion_ts"] = datetime.now(timezone.utc).isoformat()
        enriched.append(record)

    body = "\n".join(json.dumps(r) for r in enriched).encode("utf-8")
    key = "raw/pubmed/{}/pubmed_{}.json".format(ds, ds)

    _s3_client().put_object(Bucket=S3_BUCKET, Key=key, Body=body, ContentType="application/json")
    logger.info("load_pubmed_to_s3: wrote %d records to s3://%s/%s", len(enriched), S3_BUCKET, key)


def validate_pubmed(**context):
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
    dag_id="pubmed_daily",
    default_args=default_args,
    start_date=datetime(2024, 1, 1),
    schedule_interval="0 7 * * *",
    catchup=False,
) as dag:
    fetch_task = PythonOperator(
        task_id="fetch_pubmed",
        python_callable=fetch_pubmed,
    )

    filter_task = PythonOperator(
        task_id="filter_pubmed",
        python_callable=filter_pubmed,
    )

    load_task = PythonOperator(
        task_id="load_pubmed_to_s3",
        python_callable=load_pubmed_to_s3,
    )

    validate_task = PythonOperator(
        task_id="validate_pubmed",
        python_callable=validate_pubmed,
    )

    ingest_task = PythonOperator(
        task_id="ingest_to_iceberg",
        python_callable=ingest_to_iceberg,
    )

    fetch_task >> filter_task >> load_task >> validate_task >> ingest_task
