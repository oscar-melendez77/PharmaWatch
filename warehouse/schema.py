FAERS_SCHEMA = {
    "report_id": "VARCHAR",
    "drug_name": "VARCHAR",
    "reaction": "VARCHAR",
    "severity": "INTEGER",
    "age": "INTEGER",
    "weight": "FLOAT",
    "report_date": "DATE",
    "age_group": "VARCHAR",
    "is_serious": "BOOLEAN",
    "hospitalization": "INTEGER",
    "death": "INTEGER",
    "disability": "INTEGER",
    "ingestion_ts": "TIMESTAMP",
}

PUBMED_SCHEMA = {
    "article_id": "VARCHAR",
    "title": "VARCHAR",
    "abstract": "VARCHAR",
    "publish_date": "DATE",
    "drug_name": "VARCHAR",
    "abstract_length": "INTEGER",
    "publish_year": "INTEGER",
    "ingestion_ts": "TIMESTAMP",
}

REDDIT_SCHEMA = {
    "post_id": "VARCHAR",
    "subreddit": "VARCHAR",
    "title": "VARCHAR",
    "body": "VARCHAR",
    "score": "INTEGER",
    "drug_mentions": "VARCHAR",
    "drug_list": "VARCHAR",
    "created_utc": "TIMESTAMP",
    "body_length": "INTEGER",
    "hour_of_day": "INTEGER",
    "ingestion_ts": "TIMESTAMP",
}

DRUG_LABELS_SCHEMA = {
    "drug_id": "VARCHAR",
    "brand_name": "VARCHAR",
    "generic_name": "VARCHAR",
    "warnings": "VARCHAR",
    "interactions": "VARCHAR",
    "has_interactions": "BOOLEAN",
    "warnings_length": "INTEGER",
    "ingestion_ts": "TIMESTAMP",
}
