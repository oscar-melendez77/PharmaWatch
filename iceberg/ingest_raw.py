"""Land validated raw JSON from S3 into the raw Iceberg tables.

Bridges the gap between the Airflow DAGs (which drop newline-JSON in
s3://pharmawatch-data-lake/raw/<source>/...) and the Spark transformers
(which read glue_catalog.pharmawatch.<table>). Without this step the raw
Iceberg tables created by setup_iceberg.py stay empty.

Usage:
    spark-submit --packages <iceberg-packages> ingest_raw.py <source> [ds]
    spark-submit ... ingest_raw.py all 2024-01-01

Sources: faers, pubmed, openfda (-> drug_labels), reddit.
Dedup happens downstream in the Spark clean transformers, so re-running a
day simply appends; the clean layer drops duplicates by primary key.
"""
import os
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.utils import AnalysisException

S3_BUCKET = "pharmawatch-data-lake"

ICEBERG_PACKAGES = (
    "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,"
    "software.amazon.awssdk:bundle:2.24.8,"
    "software.amazon.awssdk:url-connection-client:2.24.8"
)

# source -> (target table, path builder, ordered (column, cast-expr) list)
SOURCES = {
    "faers": {
        "table": "glue_catalog.pharmawatch.faers_events",
        "exprs": [
            ("report_id", "cast(report_id as string)"),
            ("drug_name", "cast(drug_name as string)"),
            ("reaction", "cast(reaction as string)"),
            ("severity", "cast(severity as string)"),
            ("age", "cast(age as int)"),
            ("weight", "cast(weight as double)"),
            ("report_date", "to_date(cast(report_date as string), 'yyyyMMdd')"),
            ("ingestion_ts", "cast(ingestion_ts as timestamp)"),
            ("hospitalization", "coalesce(cast(hospitalization as int), 0)"),
            ("death", "coalesce(cast(death as int), 0)"),
            ("disability", "coalesce(cast(disability as int), 0)"),
        ],
    },
    "pubmed": {
        "table": "glue_catalog.pharmawatch.pubmed_articles",
        "exprs": [
            ("article_id", "cast(article_id as string)"),
            ("title", "cast(title as string)"),
            ("abstract", "cast(abstract as string)"),
            ("publish_date", "to_date(cast(publish_date as string), 'yyyy-MM-dd')"),
            ("drug_name", "cast(drug_name as string)"),
            ("ingestion_ts", "cast(ingestion_ts as timestamp)"),
        ],
    },
    "openfda": {
        "table": "glue_catalog.pharmawatch.drug_labels",
        "exprs": [
            ("drug_id", "cast(drug_id as string)"),
            ("brand_name", "cast(brand_name as string)"),
            ("generic_name", "cast(generic_name as string)"),
            ("warnings", "cast(warnings as string)"),
            ("interactions", "cast(interactions as string)"),
            ("ingestion_ts", "cast(ingestion_ts as timestamp)"),
        ],
    },
    "reddit": {
        "table": "glue_catalog.pharmawatch.reddit_mentions",
        "exprs": [
            ("post_id", "cast(post_id as string)"),
            ("subreddit", "cast(subreddit as string)"),
            ("title", "cast(title as string)"),
            ("body", "cast(body as string)"),
            ("score", "cast(score as int)"),
            ("drug_mentions", "cast(drug_mentions as string)"),
            (
                "created_utc",
                "coalesce("
                "to_timestamp(cast(created_utc as string)), "
                "cast(from_unixtime(cast(created_utc as double)) as timestamp))",
            ),
            ("ingestion_ts", "coalesce(cast(ingestion_ts as timestamp), current_timestamp())"),
        ],
    },
}


def _input_path(source, ds):
    """Build the s3a glob for a source's validated raw JSON.

    faers/pubmed/openfda partition by run date; reddit lands timestamped
    files flat under raw/reddit/ (no date folder), so match by date prefix.
    """
    base = "s3a://{}".format(S3_BUCKET)
    if source == "reddit":
        if ds:
            return "{}/raw/reddit/reddit_{}*.json".format(base, ds.replace("-", ""))
        return "{}/raw/reddit/*.json".format(base)
    if not ds:
        raise ValueError("source {} requires a ds (YYYY-MM-DD)".format(source))
    return "{}/raw/{}/{}/{}_{}.json".format(base, source, ds, source, ds)


def build_spark():
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    builder = (
        SparkSession.builder.appName("PharmaWatch Raw Ingestion")
        .config("spark.jars.packages", ICEBERG_PACKAGES)
        .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .config("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.catalog.glue_catalog.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
        .config("spark.sql.catalog.glue_catalog.warehouse", "s3://{}/iceberg/".format(S3_BUCKET))
        .config("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
        .config("spark.hadoop.fs.s3a.endpoint.region", aws_region)
    )
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        builder = (
            builder.config("spark.hadoop.fs.s3a.access.key", access_key)
            .config("spark.hadoop.fs.s3a.secret.key", secret_key)
        )
    return builder.getOrCreate()


def _ensure_columns(df, columns):
    """Add any expected source columns that are missing as typed nulls,
    so selectExpr never fails on a sparse batch."""
    existing = set(df.columns)
    for col in columns:
        if col not in existing:
            df = df.withColumn(col, F.lit(None).cast("string"))
    return df


def ingest_source(spark, source, ds):
    if source not in SOURCES:
        raise ValueError("unknown source: {}".format(source))
    spec = SOURCES[source]
    path = _input_path(source, ds)

    try:
        raw = spark.read.json(path)
    except AnalysisException:
        print("ingest_raw: no input at {} — nothing to ingest".format(path))
        return 0

    if len(raw.columns) == 0 or raw.rdd.isEmpty():
        print("ingest_raw: empty input at {}".format(path))
        return 0

    input_cols = [name for name, _ in spec["exprs"]]
    raw = _ensure_columns(raw, input_cols)

    select_exprs = ["{} as {}".format(expr, name) for name, expr in spec["exprs"]]
    df = raw.selectExpr(*select_exprs)

    count = df.count()
    df.writeTo(spec["table"]).append()
    print("ingest_raw: appended {} rows -> {}".format(count, spec["table"]))
    return count


def main(argv):
    if not argv:
        print("usage: ingest_raw.py <source|all> [ds]")
        return 2
    source = argv[0]
    ds = argv[1] if len(argv) > 1 else None

    spark = build_spark()
    try:
        targets = list(SOURCES.keys()) if source == "all" else [source]
        total = 0
        for src in targets:
            total += ingest_source(spark, src, ds)
        print("ingest_raw: done, {} rows total".format(total))
        return 0
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
