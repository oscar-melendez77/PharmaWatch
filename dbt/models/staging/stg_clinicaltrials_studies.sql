select
    concat(nct_id, '|', drug) as study_drug_key,
    nct_id,
    drug as drug_name,
    title,
    status,
    nullif(phase, '') as phase,
    enrollment,
    cast(
        case
            when length(start_date) = 7 then concat(start_date, '-01')
            else start_date
        end as date
    ) as started_on,
    array_length(adverse_events) as adverse_event_count,
    cast(ingestion_ts as timestamp) as ingested_at
from {{ source('raw', 'clinicaltrials_studies') }}
