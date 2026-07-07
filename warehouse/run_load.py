"""Read the clean Iceberg tables and load them into the active warehouse.

This is the missing caller for warehouse.loader.load(). The Spark transformers
write glue_catalog.pharmawatch_clean.*; this job reads those, coerces each
column to the warehouse schema types (schema.py), and hands the four
DataFrames to the Snowflake / BigQuery loader (WAREHOUSE env selects which).

Usage:
    spark-submit --packages <iceberg-packages> warehouse/run_load.py
"""
import sys

from pyspark.sql import SparkSession

S3_BUCKET = "pharmawatch-data-lake"

ICEBERG_PACKAGES = (
    "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.2,"
    "software.amazon.awssdk:bundle:2.24.8,"
    "software.amazon.awssdk:url-connection-client:2.24.8"
)

# clean table -> selectExpr list aligning columns/types to warehouse schema.py.
# Casting here keeps the loaders schema-clean for both Snowflake and BigQuery
# (e.g. reddit drug_list is an array in Iceberg but a VARCHAR in the warehouse).
CLEAN_TABLES = {
    "faers": (
        "glue_catalog.pharmawatch_clean.faers_events",
        [
            "cast(report_id as string) as report_id",
            "cast(drug_name as string) as drug_name",
            "cast(reaction as string) as reaction",
            "cast(severity as int) as severity",
            "cast(age as int) as age",
            "cast(weight as double) as weight",
            "cast(report_date as date) as report_date",
            "cast(age_group as string) as age_group",
            "cast(is_serious as boolean) as is_serious",
            "coalesce(cast(hospitalization as int), 0) as hospitalization",
            "coalesce(cast(death as int), 0) as death",
            "coalesce(cast(disability as int), 0) as disability",
            "cast(ingestion_ts as timestamp) as ingestion_ts",
        ],
    ),
    "pubmed": (
        "glue_catalog.pharmawatch_clean.pubmed_articles",
        [
            "cast(article_id as string) as article_id",
            "cast(title as string) as title",
            "cast(abstract as string) as abstract",
            "cast(publish_date as date) as publish_date",
            "cast(drug_name as string) as drug_name",
            "cast(abstract_length as int) as abstract_length",
            "cast(publish_year as int) as publish_year",
            "cast(ingestion_ts as timestamp) as ingestion_ts",
        ],
    ),
    "reddit": (
        "glue_catalog.pharmawatch_clean.reddit_mentions",
        [
            "cast(post_id as string) as post_id",
            "cast(subreddit as string) as subreddit",
            "cast(title as string) as title",
            "cast(body as string) as body",
            "cast(score as int) as score",
            "cast(drug_mentions as string) as drug_mentions",
            "concat_ws(',', drug_list) as drug_list",
            "cast(created_utc as timestamp) as created_utc",
            "cast(body_length as int) as body_length",
            "cast(hour_of_day as int) as hour_of_day",
            "cast(ingestion_ts as timestamp) as ingestion_ts",
        ],
    ),
    "labels": (
        "glue_catalog.pharmawatch_clean.drug_labels",
        [
            "cast(drug_id as string) as drug_id",
            "cast(brand_name as string) as brand_name",
            "cast(generic_name as string) as generic_name",
            "cast(warnings as string) as warnings",
            "cast(interactions as string) as interactions",
            "cast(has_interactions as boolean) as has_interactions",
            "cast(warnings_length as int) as warnings_length",
            "cast(ingestion_ts as timestamp) as ingestion_ts",
        ],
    ),
}


def build_spark():
    import os

    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    builder = (
        SparkSession.builder.appName("PharmaWatch Warehouse Load")
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


def _read_clean(spark, key):
    table, exprs = CLEAN_TABLES[key]
    pdf = spark.read.format("iceberg").table(table).selectExpr(*exprs).toPandas()
    print("run_load: read {} rows from {}".format(len(pdf), table))
    return pdf


def main():
    spark = build_spark()
    try:
        faers_df = _read_clean(spark, "faers")
        pubmed_df = _read_clean(spark, "pubmed")
        reddit_df = _read_clean(spark, "reddit")
        labels_df = _read_clean(spark, "labels")
    finally:
        spark.stop()

    from loader import load

    load(faers_df, pubmed_df, reddit_df, labels_df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
