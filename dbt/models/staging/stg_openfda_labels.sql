select
    drug_id as label_id,
    drug_name,
    brand_name,
    generic_name,
    warnings as warnings_text,
    interactions as interactions_text,
    warnings is not null as has_warnings,
    interactions is not null as has_interactions,
    cast(ingestion_ts as timestamp) as ingested_at
from {{ source('raw', 'openfda_labels') }}
