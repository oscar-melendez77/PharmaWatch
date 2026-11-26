select
    concat(report_id, '|', drug_name) as report_drug_key,
    report_id,
    drug_name,
    logical_or(is_serious) as is_serious,
    logical_or(was_hospitalized) as was_hospitalized,
    logical_or(resulted_in_death) as resulted_in_death,
    max(patient_age_years) as patient_age_years,
    max(patient_weight_kg) as patient_weight_kg,
    max(patient_sex) as patient_sex,
    max(n_drugs) as n_drugs,
    max(report_date) as report_date
from {{ ref('stg_faers') }}
group by report_id, drug_name
