select
    concat(report_id, '|', drug_name, '|', coalesce(reaction, '')) as faers_key,
    report_id,
    drug_name,
    reported_drug,
    reaction,
    cast(severity as int64) = 1 as is_serious,
    age_years as patient_age_years,
    weight as patient_weight_kg,
    case patient_sex
        when '1' then 'male'
        when '2' then 'female'
    end as patient_sex,
    n_drugs,
    hospitalization = 1 as was_hospitalized,
    death = 1 as resulted_in_death,
    disability = 1 as resulted_in_disability,
    parse_date('%Y%m%d', report_date) as report_date,
    cast(ingestion_ts as timestamp) as ingested_at
from {{ source('raw', 'faers') }}
