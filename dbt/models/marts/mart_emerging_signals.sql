with windows as (
    select
        w.drug_name,
        w.reaction,
        w.recent_reports,
        w.history_reports,
        w.recent_drug_reports,
        w.history_drug_reports,
        w.history_is_upper_bound,
        w.recent_window_start,
        w.recent_window_end,
        cast(w.recent_reports as float64) + 0.5 as a_recent,
        cast(w.recent_drug_reports as float64) + 0.5 as n_recent,
        cast(w.history_reports as float64) + 0.5 as a_history,
        cast(w.history_drug_reports as float64) + 0.5 as n_history
    from {{ ref('stg_faers_windows') }} as w
    left join {{ ref('excluded_reactions') }} as e
        on w.reaction = e.reaction
    where e.reaction is null
),

ratios as (
    select
        *,
        safe_divide(a_recent / n_recent, a_history / n_history) as rate_ratio,
        sqrt(1 / a_recent - 1 / n_recent + 1 / a_history - 1 / n_history) as se_ln_ratio
    from windows
),

intervals as (
    select
        *,
        exp(safe.ln(rate_ratio) - 1.96 * se_ln_ratio) as rate_ratio_lower_95
    from ratios
)

select
    concat(drug_name, '|', reaction) as drug_reaction_key,
    drug_name,
    reaction,
    recent_reports,
    history_reports,
    recent_drug_reports,
    history_drug_reports,
    safe_divide(recent_reports, recent_drug_reports) as recent_rate,
    safe_divide(history_reports, history_drug_reports) as history_rate,
    rate_ratio,
    rate_ratio_lower_95,
    history_is_upper_bound,
    recent_window_start,
    recent_window_end,
    coalesce(
        recent_reports >= {{ var('emerging_min_recent_reports') }}
        and rate_ratio_lower_95 >= {{ var('emerging_min_rate_ratio_lower_95') }},
        false
    ) as is_emerging
from intervals
