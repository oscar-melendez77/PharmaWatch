with cells as (
    select
        s.drug_name,
        s.reaction,
        s.drug_reports,
        s.reaction_reports,
        s.total_reports,
        s.reaction_total_approximate,
        cast(s.drug_reaction_reports as float64) as a,
        cast(s.drug_reports - s.drug_reaction_reports as float64) as b,
        cast(s.reaction_reports - s.drug_reaction_reports as float64) as c,
        cast(s.total_reports - s.drug_reports - s.reaction_reports + s.drug_reaction_reports as float64) as d
    from {{ ref('stg_faers_counts') }} as s
    left join {{ ref('excluded_reactions') }} as e
        on s.reaction = e.reaction
    where e.reaction is null
),

scores as (
    select
        *,
        safe_divide(a / (a + b), safe_divide(c, c + d)) as prr,
        sqrt(safe_divide(1, a) - safe_divide(1, a + b) + safe_divide(1, c) - safe_divide(1, c + d)) as se_ln_prr,
        safe_divide(a * d, b * c) as ror,
        sqrt(safe_divide(1, a) + safe_divide(1, b) + safe_divide(1, c) + safe_divide(1, d)) as se_ln_ror,
        safe_divide(
            (a + b + c + d) * pow(a * d - b * c, 2),
            (a + b) * (c + d) * (a + c) * (b + d)
        ) as chi_squared
    from cells
),

intervals as (
    select
        *,
        exp(safe.ln(prr) - 1.96 * se_ln_prr) as prr_lower_95,
        exp(safe.ln(ror) - 1.96 * se_ln_ror) as ror_lower_95,
        exp(safe.ln(ror) + 1.96 * se_ln_ror) as ror_upper_95
    from scores
)

select
    concat(drug_name, '|', reaction) as drug_reaction_key,
    drug_name,
    reaction,
    cast(a as int64) as drug_reaction_reports,
    drug_reports,
    reaction_reports,
    total_reports,
    prr,
    prr_lower_95,
    ror,
    ror_lower_95,
    ror_upper_95,
    chi_squared,
    coalesce(a >= 3 and prr >= 2 and chi_squared >= 4, false) as is_signal,
    coalesce(a >= {{ var('strong_signal_min_reports') }} and prr_lower_95 >= {{ var('strong_signal_min_prr_lower_95') }}, false) as is_strong_signal,
    reaction_total_approximate
from intervals
