with signals as (
    select
        drug_name,
        max(drug_reports) as drug_reports,
        count(*) as scored_pairs,
        countif(is_signal) as signals,
        countif(is_strong_signal) as strong_signals
    from {{ ref('mart_safety_signals') }}
    group by drug_name
),

top_signals as (
    select
        drug_name,
        array_agg(
            struct(reaction, drug_reaction_reports, prr, prr_lower_95)
            order by prr_lower_95 desc
            limit 5
        ) as top_signals
    from {{ ref('mart_safety_signals') }}
    where is_strong_signal
    group by drug_name
),

report_level as (
    select
        drug_name,
        report_id,
        logical_or(is_serious) as is_serious,
        logical_or(was_hospitalized) as was_hospitalized,
        logical_or(resulted_in_death) as resulted_in_death,
        max(patient_age_years) as patient_age_years
    from {{ ref('stg_faers') }}
    group by drug_name, report_id
),

recent as (
    select
        drug_name,
        count(*) as recent_reports,
        avg(cast(is_serious as int64)) as recent_serious_share,
        avg(cast(was_hospitalized as int64)) as recent_hospitalized_share,
        avg(cast(resulted_in_death as int64)) as recent_death_share,
        approx_quantiles(patient_age_years, 2)[offset(1)] as recent_median_age_years
    from report_level
    group by drug_name
),

articles as (
    select
        drug_name,
        count(distinct article_id) as pubmed_articles
    from {{ ref('stg_pubmed_articles') }}
    group by drug_name
),

trials as (
    select
        drug_name,
        count(distinct nct_id) as trials_with_results
    from {{ ref('stg_clinicaltrials_studies') }}
    group by drug_name
)

select
    signals.drug_name,
    signals.drug_reports,
    signals.scored_pairs,
    signals.signals,
    signals.strong_signals,
    top_signals.top_signals,
    recent.recent_reports,
    recent.recent_serious_share,
    recent.recent_hospitalized_share,
    recent.recent_death_share,
    recent.recent_median_age_years,
    articles.pubmed_articles,
    trials.trials_with_results
from signals
left join top_signals on signals.drug_name = top_signals.drug_name
left join recent on signals.drug_name = recent.drug_name
left join articles on signals.drug_name = articles.drug_name
left join trials on signals.drug_name = trials.drug_name
