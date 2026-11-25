{{ config(severity='warn') }}

select
    faers_key,
    patient_age_years
from {{ ref('stg_faers') }}
where patient_age_years < 0
   or patient_age_years > 120
