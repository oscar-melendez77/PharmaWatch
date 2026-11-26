import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query

# 1. load everything once, when the service starts
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ARTIFACT_DIR = BASE_DIR / "artifacts"

profile = pd.read_parquet(DATA_DIR / "drug_profile.parquet")
signals = pd.read_parquet(DATA_DIR / "safety_signals.parquet")
emerging = pd.read_parquet(DATA_DIR / "emerging_signals.parquet")
booster = lgb.Booster(model_file=str(ARTIFACT_DIR / "risk_model.txt"))

# the monthly history is new, an older snapshot may not have it yet, then the trends view says so
MONTHLY_COLUMNS = ["drug_name", "month", "reports", "serious_reports", "hospitalized_reports", "death_reports"]
if (DATA_DIR / "monthly_trends.parquet").exists():
    monthly = pd.read_parquet(DATA_DIR / "monthly_trends.parquet")
else:
    monthly = pd.DataFrame(columns=MONTHLY_COLUMNS)
with open(ARTIFACT_DIR / "feature_info.json") as f:
    feature_info = json.load(f)

# volume per drug: the last 12 months against the average year before
window_start = pd.Timestamp(emerging["recent_window_start"].iloc[0])
HISTORY_YEARS = (window_start - pd.Timestamp("2004-01-01")).days / 365.25
DATA_THROUGH = str(emerging["recent_window_end"].iloc[0])
TOTAL_FAERS_REPORTS = int(signals["total_reports"].iloc[0])

volume = emerging.groupby("drug_name")[["recent_drug_reports", "history_drug_reports"]].first().reset_index()
volume["yearly_average_before"] = volume["history_drug_reports"] / HISTORY_YEARS
volume["volume_vs_average"] = volume["recent_drug_reports"] / volume["yearly_average_before"]
profile = profile.merge(volume[["drug_name", "recent_drug_reports", "yearly_average_before", "volume_vs_average"]], on="drug_name", how="left")
emerging["history_per_year"] = emerging["history_reports"] / HISTORY_YEARS

DRUG_NAMES = set(profile["drug_name"])
SIGNAL_COLUMNS = ["reaction", "drug_reaction_reports", "prr", "prr_lower_95", "ror", "ror_lower_95", "chi_squared", "is_strong_signal", "reaction_total_approximate"]
EMERGING_COLUMNS = ["reaction", "recent_reports", "history_reports", "history_per_year", "recent_rate", "history_rate", "rate_ratio", "rate_ratio_lower_95", "history_is_upper_bound", "is_emerging"]
SIGNAL_SORT_COLUMNS = ["prr_lower_95", "prr", "ror_lower_95", "drug_reaction_reports", "chi_squared"]

app = FastAPI(
    title="PharmaWatch API",
    description=(
        "Drug safety signals computed from FDA adverse event reports (FAERS). "
        "A signal means a reaction is reported unusually often with a drug, it does not prove the drug causes it. "
        "Not medical advice."
    ),
    version="1.0.0",
)


# 2. routes
@app.get("/health")
def health():
    """Shows the service is up and how much data it holds."""
    return {"status": "ok", "drugs": int(len(profile)), "signal_pairs": int(len(signals)), "emerging_pairs": int(len(emerging))}


@app.get("/overview")
def overview():
    """Portfolio view: headline numbers and one row per drug."""
    columns = ["drug_name", "drug_reports", "recent_drug_reports", "volume_vs_average", "recent_serious_share", "recent_hospitalized_share", "recent_death_share", "strong_signals"]
    table = profile[columns].sort_values("drug_reports", ascending=False)
    return {
        "data_through": DATA_THROUGH,
        "total_faers_reports": TOTAL_FAERS_REPORTS,
        "drugs": int(len(profile)),
        "reports_on_tracked_drugs": int(profile["drug_reports"].sum()),
        "reports_last_12_months": int(profile["recent_drug_reports"].sum()),
        "drugs_table": json.loads(table.to_json(orient="records")),
    }


@app.get("/trends")
def trends(drugs: str):
    """Reports per month for one or more drugs, given as names separated by commas."""
    names = [name.strip() for name in drugs.split(",") if name.strip() != ""]
    for name in names:
        if name not in DRUG_NAMES:
            raise HTTPException(status_code=404, detail="Unknown drug: " + name)

    rows = monthly[monthly["drug_name"].isin(names)].sort_values(["drug_name", "month"])
    return json.loads(rows[MONTHLY_COLUMNS].to_json(orient="records"))


@app.get("/drugs")
def list_drugs():
    """All drugs with their report and signal counts."""
    columns = ["drug_name", "drug_reports", "signals", "strong_signals"]
    return json.loads(profile[columns].sort_values("drug_name").to_json(orient="records"))


@app.get("/drugs/{drug_name}/profile")
def drug_profile(drug_name: str):
    """One drug's summary: report totals, signal counts and the profile of its newest reports."""
    row = profile[profile["drug_name"] == drug_name]
    if row.empty:
        raise HTTPException(status_code=404, detail="Unknown drug: " + drug_name)
    return json.loads(row.to_json(orient="records"))[0]


@app.get("/drugs/{drug_name}/top-reactions")
def drug_top_reactions(drug_name: str, limit: int = Query(10, ge=1, le=50)):
    """The reactions reported most often with the drug, with their share of the drug's reports."""
    if drug_name not in DRUG_NAMES:
        raise HTTPException(status_code=404, detail="Unknown drug: " + drug_name)

    rows = signals[signals["drug_name"] == drug_name].sort_values("drug_reaction_reports", ascending=False).head(limit).copy()
    rows["share_of_drug_reports"] = rows["drug_reaction_reports"] / rows["drug_reports"]
    return json.loads(rows[["reaction", "drug_reaction_reports", "share_of_drug_reports", "prr"]].to_json(orient="records"))


@app.get("/drugs/{drug_name}/signals")
def drug_signals(
    drug_name: str,
    limit: int = Query(20, ge=1, le=200),
    strong_only: bool = True,
    min_reports: int = Query(10, ge=3),
    sort_by: str = "prr_lower_95",
):
    """Reactions reported disproportionately often with the drug, strongest first."""
    if drug_name not in DRUG_NAMES:
        raise HTTPException(status_code=404, detail="Unknown drug: " + drug_name)
    if sort_by not in SIGNAL_SORT_COLUMNS:
        raise HTTPException(status_code=422, detail="sort_by must be one of " + ", ".join(SIGNAL_SORT_COLUMNS))

    rows = signals[(signals["drug_name"] == drug_name) & (signals["drug_reaction_reports"] >= min_reports)]
    if strong_only:
        rows = rows[rows["is_strong_signal"]]
    rows = rows.sort_values(sort_by, ascending=False).head(limit)
    return json.loads(rows[SIGNAL_COLUMNS].to_json(orient="records"))


@app.get("/drugs/{drug_name}/emerging")
def drug_emerging(drug_name: str, limit: int = Query(20, ge=1, le=200), emerging_only: bool = True):
    """Reactions whose share of the drug's reports rose in the last year compared with the years before."""
    if drug_name not in DRUG_NAMES:
        raise HTTPException(status_code=404, detail="Unknown drug: " + drug_name)

    rows = emerging[emerging["drug_name"] == drug_name]
    if emerging_only:
        rows = rows[rows["is_emerging"]]
    rows = rows.sort_values("rate_ratio_lower_95", ascending=False).head(limit)
    return json.loads(rows[EMERGING_COLUMNS].to_json(orient="records"))


@app.get("/compare")
def compare(drug_a: str, drug_b: str):
    """Two drugs side by side, with the reactions that stand out for both."""
    for name in (drug_a, drug_b):
        if name not in DRUG_NAMES:
            raise HTTPException(status_code=404, detail="Unknown drug: " + name)

    result = {}
    for key, name in (("a", drug_a), ("b", drug_b)):
        drug_row = json.loads(profile[profile["drug_name"] == name].to_json(orient="records"))[0]
        top = signals[(signals["drug_name"] == name) & signals["is_strong_signal"]].sort_values("prr_lower_95", ascending=False).head(10)
        drug_row["top_signals"] = json.loads(top[SIGNAL_COLUMNS].to_json(orient="records"))
        result[key] = drug_row

    # reactions that are a signal for both drugs, ranked by the weaker of the two scores
    side_a = signals[(signals["drug_name"] == drug_a) & signals["is_strong_signal"]][["reaction", "drug_reaction_reports", "prr_lower_95"]]
    side_b = signals[(signals["drug_name"] == drug_b) & signals["is_strong_signal"]][["reaction", "drug_reaction_reports", "prr_lower_95"]]
    shared = side_a.merge(side_b, on="reaction", suffixes=("_a", "_b"))
    shared["weaker_score"] = shared[["prr_lower_95_a", "prr_lower_95_b"]].min(axis=1)
    shared = shared.sort_values("weaker_score", ascending=False).head(10)
    result["shared"] = json.loads(shared.to_json(orient="records"))
    return result


@app.get("/risk")
def risk(
    drug_name: str,
    age_years: float | None = Query(None, ge=0, le=120),
    weight_kg: float | None = Query(None, gt=0, le=400),
    sex: str | None = None,
    n_drugs: int | None = Query(None, ge=1, le=100),
):
    """Share of similar FAERS reports that were marked serious, with what pushed it up or down."""
    if drug_name not in DRUG_NAMES:
        raise HTTPException(status_code=404, detail="Unknown drug: " + drug_name)
    if sex not in (None, "male", "female"):
        raise HTTPException(status_code=422, detail="sex must be male or female, or left out")

    categories = feature_info["categories"]
    row = pd.DataFrame({
        "drug_name": pd.Categorical([drug_name], categories=categories["drug_name"]),
        "patient_sex": pd.Categorical([sex], categories=categories["patient_sex"]),
        "patient_age_years": [np.nan if age_years is None else float(age_years)],
        "patient_weight_kg": [np.nan if weight_kg is None else float(weight_kg)],
        "n_drugs": [np.nan if n_drugs is None else float(n_drugs)],
    })[feature_info["features"]]

    share_serious = float(booster.predict(row)[0])
    contributions = booster.predict(row, pred_contrib=True)[0]

    given = {
        "drug_name": drug_name,
        "patient_sex": sex,
        "patient_age_years": age_years,
        "patient_weight_kg": weight_kg,
        "n_drugs": n_drugs,
    }
    explanation = []
    for index, feature in enumerate(feature_info["features"]):
        explanation.append({"feature": feature, "value": given[feature], "log_odds_push": float(contributions[index])})

    drug_row = profile[profile["drug_name"] == drug_name]
    drug_share = drug_row["recent_serious_share"].iloc[0]
    return {
        "share_serious": share_serious,
        "drug_recent_serious_share": None if pd.isna(drug_share) else float(drug_share),
        "base_log_odds": float(contributions[-1]),
        "explanation": explanation,
        "note": "Share of similar FAERS reports marked serious. It is not a patient's personal risk and not medical advice.",
    }
