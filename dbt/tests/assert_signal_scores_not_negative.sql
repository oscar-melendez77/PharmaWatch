select
    drug_reaction_key
from {{ ref('mart_safety_signals') }}
where prr < 0
   or ror < 0
   or chi_squared < 0
