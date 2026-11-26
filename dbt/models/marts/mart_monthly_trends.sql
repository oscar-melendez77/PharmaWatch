select
    drug_month_key,
    drug_name,
    month,
    reports,
    serious_reports,
    hospitalized_reports,
    death_reports,
    safe_divide(serious_reports, reports) as serious_share,
    safe_divide(hospitalized_reports, reports) as hospitalized_share,
    safe_divide(death_reports, reports) as death_share
from {{ ref('stg_faers_monthly') }}
