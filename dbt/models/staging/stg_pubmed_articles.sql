select
    concat(article_id, '|', drug_name) as article_drug_key,
    article_id,
    drug_name,
    title,
    abstract,
    cast(publish_date as date) as published_on,
    cast(ingestion_ts as timestamp) as ingested_at
from {{ source('raw', 'pubmed_articles') }}
