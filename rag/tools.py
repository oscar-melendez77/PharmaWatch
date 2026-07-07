"""Data-access helpers backing the agent's tools.

Kept separate from agent.py so they carry no LangChain/Groq dependency and can
be imported and unit-tested with just pandas. Each function turns one collected
data source into a compact text block the LLM can reason over: model risk scores
(+ personalization), Reddit community sentiment, and FDA label info.
"""
import os
import sys
from pathlib import Path

_ML_DIR = str(Path(__file__).resolve().parent.parent / "ml")
if _ML_DIR not in sys.path:
    sys.path.insert(0, _ML_DIR)

from predict import _reddit_kpis, _split_interactions, _label_severity


def format_risk_summary(result):
    if result is None:
        return "No risk data available."
    lines = [
        "Serious reaction: {:.1f}%".format(float(result.get("serious_reaction_pct", 0.0))),
        "Hospitalization: {:.1f}%".format(float(result.get("hospitalization_pct", 0.0))),
        "Death: {:.1f}%".format(float(result.get("death_pct", 0.0))),
        "Disability: {:.1f}%".format(float(result.get("disability_pct", 0.0))),
        "Risk label: {}".format(result.get("risk_label", "UNKNOWN")),
        "Top risk factor: {}".format(result.get("top_risk_factor", "n/a")),
        "Dependency mention rate: {:.1f}%".format(float(result.get("dependency_mention_rate", 0.0))),
        "Withdrawal mention rate: {:.1f}%".format(float(result.get("withdrawal_mention_rate", 0.0))),
        "Community concern score: {:.1f}".format(float(result.get("community_concern_score", 0.0))),
        "Research risk consensus: {:.1f}%".format(float(result.get("research_risk_consensus", 0.0))),
        "Total research papers: {}".format(int(result.get("total_research_papers", 0))),
        "Label warning severity: {}".format(result.get("label_warning_severity", "UNKNOWN")),
    ]
    return "\n".join(lines)


def risk_scores_text(drug_name, master_df, reddit_df, pubmed_df, labels_df, user_profile=None):
    """Model risk scores for a drug, personalized to the profile when given."""
    if master_df is None:
        return "Risk data unavailable: master profile not loaded."
    from predict import predict_risk

    profile = user_profile or {}
    age = profile.get("age") or 30
    weight = profile.get("weight") or 70

    result = predict_risk(drug_name, age, weight, master_df, reddit_df, pubmed_df, labels_df)
    if result is None:
        return "No risk data found for {}.".format(drug_name)

    text = format_risk_summary(result)

    if user_profile:
        from personalize import build_factors, personalize

        factors = build_factors(
            age=age,
            sex=profile.get("sex"),
            weight=weight,
            smoker=profile.get("smoker"),
            alcohol=profile.get("alcohol"),
            concurrent_meds=profile.get("concurrent_meds"),
            pregnant=profile.get("pregnant"),
        )
        p = personalize(
            {
                "serious": result.get("serious_reaction_pct", 0.0),
                "hospitalization": result.get("hospitalization_pct", 0.0),
                "death": result.get("death_pct", 0.0),
                "disability": result.get("disability_pct", 0.0),
            },
            factors,
        )
        if p["applied"]:
            text += "\n\nPersonalized for this profile:"
            text += "\n  Serious {:.1f}% | Hospitalization {:.1f}% | Death {:.1f}% | Disability {:.1f}% | Risk {}".format(
                p["serious_reaction_pct"], p["hospitalization_pct"],
                p["death_pct"], p["disability_pct"], p["risk_label"],
            )
            for a in p["adjustments"]:
                text += "\n  - {} (x{:.2f} serious): {}".format(
                    a["factor"], a["serious_multiplier"], a["detail"]
                )

    return text


def community_sentiment_text(drug_name, reddit_df):
    """Reddit dependency / withdrawal / concern signal for a drug."""
    if reddit_df is None or getattr(reddit_df, "empty", True):
        return "No community (Reddit) data available for {}.".format(drug_name)
    dep, wd, concern = _reddit_kpis(drug_name, reddit_df)
    return (
        "Reddit community signal for {}: dependency mentions {:.1f}%, "
        "withdrawal mentions {:.1f}%, community concern score {:.1f}/100."
    ).format(drug_name, dep, wd, concern)


def label_info_text(drug_name, labels_df):
    """FDA label warnings + interactions for a drug."""
    if labels_df is None or getattr(labels_df, "empty", True) or "brand_name" not in getattr(labels_df, "columns", []):
        return "No FDA label data available for {}.".format(drug_name)

    match = labels_df[labels_df["brand_name"].astype(str).str.upper() == drug_name.upper()]
    if match.empty:
        return "No FDA label found for {}.".format(drug_name)

    row = match.iloc[0]
    warnings = str(row.get("warnings") or "").strip()
    interactions = _split_interactions(row.get("interactions"))
    severity = _label_severity(row.get("warnings_length"))

    parts = ["FDA label for {}:".format(drug_name), "  Warning severity: {}".format(severity)]
    if warnings:
        parts.append("  Warnings: {}".format(warnings[:400]))
    parts.append(
        "  Known interactions: {}".format(", ".join(interactions) if interactions else "none listed")
    )
    return "\n".join(parts)
