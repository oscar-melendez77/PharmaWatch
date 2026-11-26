{{ config(severity='warn') }}

select
    drug_month_key
from {{ ref('stg_faers_monthly') }}
where serious_reports > reports
   or hospitalized_reports > serious_reports
   or death_reports > serious_reports
   or reports < 0
