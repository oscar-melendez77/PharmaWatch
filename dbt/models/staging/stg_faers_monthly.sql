select
    concat(drug_name, '|', month) as drug_month_key,
    drug_name,
    cast(month as date) as month,
    reports,
    serious_reports,
    hospitalized_reports,
    death_reports,
    cast(ingestion_ts as timestamp) as ingested_at
from {{ source('raw', 'faers_monthly') }}
