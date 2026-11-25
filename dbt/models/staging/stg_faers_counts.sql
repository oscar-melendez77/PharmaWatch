select
    concat(drug_name, '|', reaction) as drug_reaction_key,
    drug_name,
    reaction,
    drug_reaction_reports,
    drug_reports,
    reaction_reports,
    total_reports,
    reaction_total_approximate,
    cast(ingestion_ts as timestamp) as ingested_at
from {{ source('raw', 'faers_counts') }}
