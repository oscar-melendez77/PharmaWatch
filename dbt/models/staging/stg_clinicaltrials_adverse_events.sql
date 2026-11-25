select
    concat(studies.nct_id, '|', studies.drug, '|', event.term, '|', cast(event.is_serious as string)) as event_key,
    studies.nct_id,
    studies.drug as drug_name,
    event.term as event_term,
    event.is_serious,
    event.num_affected,
    event.num_at_risk
from {{ source('raw', 'clinicaltrials_studies') }} as studies
cross join unnest(studies.adverse_events) as event
