import os
import json

import numpy as np
import pandas as pd

# mlflow / shap are imported lazily inside the functions that need them so this
# module (and its pure KPI helpers) can be imported without the heavy ML stack.
from features import build_user_profile, FEATURE_COLUMNS

MODEL_NAMES = {
    "serious": "pharmawatch-serious",
    "hospitalization": "pharmawatch-hospitalization",
    "death": "pharmawatch-death",
    "disability": "pharmawatch-disability",
}

DEPENDENCY_TERMS = ["addicted", "addiction", "dependent", "dependence", "habit", "hooked"]
WITHDRAWAL_TERMS = ["withdrawal", "withdrawing", "cold turkey", "stopped taking", "quitting"]
NEGATIVE_RESEARCH_TERMS = ["risk", "adverse", "danger", "toxic", "harmful", "fatal"]


def _load_models():
    import mlflow
    import mlflow.lightgbm

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    models = {}
    for key, name in MODEL_NAMES.items():
        uri = "models:/{}/Production".format(name)
        try:
            models[key] = mlflow.lightgbm.load_model(uri)
        except Exception:
            print("missing production model: {}".format(name))
            return None
    return models


def _contains_any(text, terms):
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _reddit_kpis(drug_name, reddit_df):
    if reddit_df is None or reddit_df.empty:
        return 0.0, 0.0, 0.0

    mask = reddit_df["drug_mentions"].astype(str).str.contains(drug_name, case=False, na=False)
    subset = reddit_df[mask]
    if subset.empty:
        return 0.0, 0.0, 0.0

    total = len(subset)
    bodies = subset["body"].fillna("").astype(str).tolist()
    dep_hits = sum(1 for b in bodies if _contains_any(b, DEPENDENCY_TERMS))
    wd_hits = sum(1 for b in bodies if _contains_any(b, WITHDRAWAL_TERMS))
    dep_rate = (dep_hits / total) * 100.0
    wd_rate = (wd_hits / total) * 100.0

    all_scores = pd.to_numeric(reddit_df["score"], errors="coerce").dropna()
    if all_scores.empty:
        concern = 0.0
    else:
        s_min = float(all_scores.min())
        s_max = float(all_scores.max())
        drug_scores = pd.to_numeric(subset["score"], errors="coerce").dropna()
        if drug_scores.empty:
            concern = 0.0
        elif s_max == s_min:
            concern = 50.0
        else:
            avg_drug_score = float(drug_scores.mean())
            concern = ((avg_drug_score - s_min) / (s_max - s_min)) * 100.0
            concern = max(0.0, min(100.0, concern))

    return dep_rate, wd_rate, concern


def _pubmed_kpis(drug_name, pubmed_df):
    if pubmed_df is None or pubmed_df.empty:
        return 0.0, 0
    subset = pubmed_df[pubmed_df["drug_name"].astype(str).str.upper() == drug_name.upper()]
    total = len(subset)
    if total == 0:
        return 0.0, 0
    abstracts = subset["abstract"].fillna("").astype(str).tolist()
    negatives = sum(1 for a in abstracts if _contains_any(a, NEGATIVE_RESEARCH_TERMS))
    consensus = (negatives / total) * 100.0
    return consensus, int(total)


def _top_reactions(master_row):
    if "adv_top_reactions" in master_row.index:
        raw = master_row.get("adv_top_reactions")
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return parsed[:5]
            except (ValueError, TypeError):
                pass
    if "adv_most_common_reaction" in master_row.index:
        reaction = master_row.get("adv_most_common_reaction")
        if isinstance(reaction, str) and reaction:
            return [{"reaction": reaction, "frequency_pct": 100.0}]
    return []


def _label_severity(warnings_length):
    if warnings_length is None or (isinstance(warnings_length, float) and np.isnan(warnings_length)):
        return "LOW"
    try:
        length = float(warnings_length)
    except (TypeError, ValueError):
        return "LOW"
    if length < 500:
        return "LOW"
    if length < 2000:
        return "MEDIUM"
    return "HIGH"


def _split_interactions(raw):
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _risk_label(serious_pct):
    if serious_pct < 30:
        return "LOW"
    if serious_pct < 70:
        return "MEDIUM"
    return "HIGH"


def _shap_contributions(model, X):
    import shap

    explainer = shap.TreeExplainer(model)
    raw = explainer.shap_values(X)
    if isinstance(raw, list):
        raw = raw[1]
    arr = np.asarray(raw)
    if arr.ndim == 2:
        row = arr[0]
    else:
        row = arr
    feature_names = list(X.columns)
    contribs = [
        {"feature": fname, "contribution": float(val)}
        for fname, val in zip(feature_names, row)
    ]
    contribs.sort(key=lambda d: abs(d["contribution"]), reverse=True)
    return contribs


def predict_risk(drug_name, age, weight, master_df, reddit_df, pubmed_df, labels_df):
    models = _load_models()
    if models is None:
        return None

    profile = build_user_profile(drug_name, age, weight, master_df)
    if profile is None:
        print("drug not found: {}".format(drug_name))
        return None

    X = profile[FEATURE_COLUMNS]

    predictions_pct = {}
    for key, model in models.items():
        proba = float(model.predict_proba(X)[:, 1][0])
        predictions_pct[key] = proba * 100.0

    serious_pct = predictions_pct["serious"]
    risk_label = _risk_label(serious_pct)

    contribs = _shap_contributions(models["serious"], X)
    top_risk_factor = contribs[0]["feature"] if contribs else None

    dep_rate, wd_rate, concern = _reddit_kpis(drug_name, reddit_df)
    consensus, total_papers = _pubmed_kpis(drug_name, pubmed_df)

    master_row = master_df[master_df["drug_name"] == drug_name].iloc[0]
    top_reactions = _top_reactions(master_row)

    warnings_length = master_row.get("lbl_warnings_length") if "lbl_warnings_length" in master_row.index else None
    interactions_raw = master_row.get("lbl_interactions") if "lbl_interactions" in master_row.index else None

    if labels_df is not None and not labels_df.empty and "brand_name" in labels_df.columns:
        lbl_match = labels_df[labels_df["brand_name"].astype(str).str.upper() == drug_name.upper()]
        if not lbl_match.empty:
            row = lbl_match.iloc[0]
            if "warnings_length" in row.index and pd.notna(row.get("warnings_length")):
                warnings_length = row["warnings_length"]
            if "interactions" in row.index and pd.notna(row.get("interactions")):
                interactions_raw = row["interactions"]

    label_severity = _label_severity(warnings_length)
    known_interactions = _split_interactions(interactions_raw)

    return {
        "serious_reaction_pct": serious_pct,
        "hospitalization_pct": predictions_pct["hospitalization"],
        "death_pct": predictions_pct["death"],
        "disability_pct": predictions_pct["disability"],
        "risk_label": risk_label,
        "shap_values": contribs,
        "top_risk_factor": top_risk_factor,
        "dependency_mention_rate": dep_rate,
        "withdrawal_mention_rate": wd_rate,
        "community_concern_score": concern,
        "research_risk_consensus": consensus,
        "total_research_papers": total_papers,
        "top_5_reactions": top_reactions,
        "label_warning_severity": label_severity,
        "known_interactions": known_interactions,
    }
