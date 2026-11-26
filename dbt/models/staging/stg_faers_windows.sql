select
    concat(drug_name, '|', reaction) as drug_reaction_key,
    drug_name,
    reaction,
    recent_reports,
    history_reports,
    recent_drug_reports,
    history_drug_reports,
    history_is_upper_bound,
    cast(recent_window_start as date) as recent_window_start,
    cast(recent_window_end as date) as recent_window_end,
    cast(ingestion_ts as timestamp) as ingested_at
from {{ source('raw', 'faers_windows') }}
