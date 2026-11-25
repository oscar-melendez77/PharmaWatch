select
    drug_reaction_key
from {{ ref('stg_faers_counts') }}
where drug_reaction_reports > drug_reports
   or drug_reaction_reports > reaction_reports
   or drug_reports > total_reports
   or reaction_reports > total_reports
