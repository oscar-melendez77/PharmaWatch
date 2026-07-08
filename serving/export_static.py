"""Export the dashboard dataset to docs/data/ for the GitHub Pages site.

Live-capable, sample by default: if API_BASE_URL points at a running PharmaWatch
API, per-drug profiles come from real /predict; otherwise stable synthetic
fixtures are generated so the dashboard renders with zero infra. Either way this
also derives the aggregate datasets the charts need (stats, model metrics,
feature importance, reaction leaderboard, research-vs-community, source stats,
pipeline runs). Stdlib only, so it runs in CI unchanged.

Usage:
    python serving/export_static.py            # sample unless API_BASE_URL is set
    API_BASE_URL=https://... python serving/export_static.py   # live
"""
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

DOCS_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data"
)

DRUGS = [
    {"drug_name": "Adderall", "age": 28, "weight": 70},
    {"drug_name": "Oxycodone", "age": 45, "weight": 80},
    {"drug_name": "Xanax", "age": 34, "weight": 65},
    {"drug_name": "Fentanyl", "age": 52, "weight": 75},
    {"drug_name": "Ibuprofen", "age": 30, "weight": 70},
    {"drug_name": "Metformin", "age": 60, "weight": 85},
    {"drug_name": "Warfarin", "age": 68, "weight": 78},
    {"drug_name": "Lisinopril", "age": 58, "weight": 82},
]

FEATURE_COLUMNS = [
    "drug_name", "age_group", "adv_avg_age", "adv_serious_reports",
    "adv_total_reports", "sent_total_mentions", "sent_avg_score",
    "res_total_papers", "lbl_warnings_length", "lbl_has_interactions",
]

FEATURE_LABELS = {
    "drug_name": "Drug identity",
    "age_group": "Age group",
    "adv_avg_age": "Avg patient age",
    "adv_serious_reports": "Serious report count",
    "adv_total_reports": "Total report volume",
    "sent_total_mentions": "Reddit mention volume",
    "sent_avg_score": "Reddit engagement",
    "res_total_papers": "Research volume",
    "lbl_warnings_length": "Label warning length",
    "lbl_has_interactions": "Has interactions",
}

REACTIONS = [
    "Nausea", "Headache", "Dizziness", "Insomnia", "Tachycardia",
    "Anxiety", "Vomiting", "Somnolence", "Fatigue", "Tremor",
]
# signature adverse reactions so the sample data stays clinically plausible
# (a domain reader should recognise each drug's real hallmark effects)
SIGNATURE = {
    "Fentanyl": ["Respiratory depression", "Sedation"],
    "Oxycodone": ["Respiratory depression", "Constipation"],
    "Xanax": ["Drowsiness", "Dependence"],
    "Adderall": ["Insomnia", "Tachycardia"],
    "Warfarin": ["Bleeding", "Bruising"],
    "Lisinopril": ["Dry cough", "Dizziness"],
    "Metformin": ["Nausea", "Diarrhea"],
    "Ibuprofen": ["GI irritation", "Heartburn"],
}
INTERACTIONS = [
    "MAO inhibitors", "SSRIs", "Warfarin", "Alcohol",
    "Benzodiazepines", "NSAIDs", "Beta blockers",
]
SEVERITY_BIAS = {
    "Fentanyl": 0.85, "Oxycodone": 0.72, "Xanax": 0.6, "Adderall": 0.5,
    "Warfarin": 0.55, "Lisinopril": 0.3, "Metformin": 0.25, "Ibuprofen": 0.2,
}
AGE_BUCKETS = ["0-17", "18-34", "35-64", "65+"]


def _risk_label(serious_pct):
    if serious_pct < 30:
        return "LOW"
    if serious_pct < 70:
        return "MEDIUM"
    return "HIGH"


def _sample_profile(drug):
    name = drug["drug_name"]
    rng = random.Random(name)
    bias = SEVERITY_BIAS.get(name, 0.4)

    serious = round(min(97.0, max(3.0, bias * 100 + rng.uniform(-12, 12))), 1)
    hosp = round(min(serious, max(1.0, serious * rng.uniform(0.4, 0.75))), 1)
    death = round(max(0.2, serious * rng.uniform(0.03, 0.15)), 1)
    disability = round(max(0.5, serious * rng.uniform(0.1, 0.3)), 1)

    # signature reactions first (higher frequency), then fill to 5 uniquely
    pool = list(dict.fromkeys(SIGNATURE.get(name, []) + rng.sample(REACTIONS, 5)))[:5]
    freqs = sorted((rng.uniform(5, 30) for _ in range(len(pool))), reverse=True)
    top_5 = [{"reaction": r, "frequency_pct": round(f, 1)} for r, f in zip(pool, freqs)]

    shap = [{"feature": f, "contribution": round(rng.uniform(-1.0, 1.0) * bias, 3)} for f in FEATURE_COLUMNS]
    shap.sort(key=lambda d: abs(d["contribution"]), reverse=True)

    # warning severity should track how dangerous the drug is, not be random
    if bias > 0.6:
        warning_sev = "HIGH"
    elif bias > 0.4:
        warning_sev = rng.choice(["MEDIUM", "HIGH"])
    else:
        warning_sev = rng.choice(["LOW", "MEDIUM"])

    # age distribution of adverse reports (weights shift with drug)
    raw = [rng.uniform(0.4, 1.0) for _ in AGE_BUCKETS]
    if name in ("Warfarin", "Lisinopril", "Metformin"):
        raw[3] *= 2.4  # skews older
    if name in ("Adderall", "Xanax"):
        raw[1] *= 2.2  # skews younger
    tot = sum(raw)
    age_distribution = {b: round(v / tot * 100, 1) for b, v in zip(AGE_BUCKETS, raw)}

    return {
        "drug_name": name,
        "serious_reaction_pct": serious,
        "hospitalization_pct": hosp,
        "death_pct": death,
        "disability_pct": disability,
        "risk_label": _risk_label(serious),
        "dependency_mention_rate": round(bias * rng.uniform(10, 45), 1),
        "withdrawal_mention_rate": round(bias * rng.uniform(8, 40), 1),
        "community_concern_score": round(rng.uniform(20, 90), 1),
        "research_risk_consensus": round(rng.uniform(15, 80), 1),
        "total_research_papers": rng.randint(3, 120),
        "total_reports": rng.randint(400, 9000),
        "age_distribution": age_distribution,
        "top_5_reactions": top_5,
        "label_warning_severity": warning_sev,
        "known_interactions": rng.sample(INTERACTIONS, rng.randint(1, 4)),
        "shap_values": shap,
        "top_risk_factor": shap[0]["feature"],
    }


def _live_profile(drug, base_url):
    payload = json.dumps(drug).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/predict",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        prof = json.loads(resp.read().decode("utf-8"))
    # live API may omit demo-only aggregate fields; backfill from sample so
    # the aggregate charts still have data
    sample = _sample_profile(drug)
    for k in ("total_reports", "age_distribution"):
        prof.setdefault(k, sample[k])
    return prof


def build_profiles():
    base_url = os.environ.get("API_BASE_URL")
    mode = "sample"
    profiles = []
    for drug in DRUGS:
        if base_url:
            try:
                profiles.append(_live_profile(drug, base_url))
                mode = "live"
                continue
            except (urllib.error.URLError, ValueError, TimeoutError) as exc:
                print("live fetch failed for {} ({}); using sample".format(drug["drug_name"], exc))
        profiles.append(_sample_profile(drug))
    profiles.sort(key=lambda p: p["serious_reaction_pct"], reverse=True)
    return mode, profiles


def _model_metrics():
    base = {"Serious": 0.87, "Hospitalization": 0.83, "Death": 0.90, "Disability": 0.80}
    out = []
    for name, auc in base.items():
        rng = random.Random("model-" + name)
        out.append({
            "model": name,
            "auc": round(auc + rng.uniform(-0.02, 0.02), 3),
            "f1": round(0.62 + rng.uniform(-0.06, 0.08), 3),
            "log_loss": round(0.33 + rng.uniform(-0.05, 0.06), 3),
        })
    return out


def _feature_importance(profiles):
    totals = {f: 0.0 for f in FEATURE_COLUMNS}
    for p in profiles:
        for s in p.get("shap_values", []):
            totals[s["feature"]] = totals.get(s["feature"], 0.0) + abs(s["contribution"])
    items = [
        {"feature": FEATURE_LABELS.get(f, f), "importance": round(v / max(len(profiles), 1), 3)}
        for f, v in totals.items()
    ]
    items.sort(key=lambda d: d["importance"], reverse=True)
    return items


def _reaction_leaderboard(profiles):
    agg = {}
    for p in profiles:
        for r in p.get("top_5_reactions", []):
            agg[r["reaction"]] = agg.get(r["reaction"], 0.0) + r["frequency_pct"]
    items = [{"reaction": k, "score": round(v, 1)} for k, v in agg.items()]
    items.sort(key=lambda d: d["score"], reverse=True)
    return items[:8]


def _research_community(profiles):
    return [
        {
            "drug": p["drug_name"],
            "research": p["research_risk_consensus"],
            "community": p["community_concern_score"],
            "serious": p["serious_reaction_pct"],
            "risk_label": p["risk_label"],
        }
        for p in profiles
    ]


def _source_stats(profiles):
    return [
        {"source": "FDA FAERS", "code": "FAERS", "records": sum(p.get("total_reports", 0) for p in profiles),
         "unit": "adverse-event reports", "note": "Serious reaction, hospitalization, death & disability signals"},
        {"source": "PubMed", "code": "PUBMED", "records": sum(p.get("total_research_papers", 0) for p in profiles),
         "unit": "research abstracts", "note": "Semantic index powering the research digest + RAG agent"},
        {"source": "OpenFDA Labels", "code": "OPENFDA", "records": len(profiles),
         "unit": "structured drug labels", "note": "Boxed warnings, interactions & warning severity"},
        {"source": "Reddit", "code": "REDDIT", "records": sum(int(p.get("community_concern_score", 0) * 40) for p in profiles),
         "unit": "community posts", "note": "Dependency, withdrawal & real-world concern signal"},
    ]


def _pipeline_runs(now):
    stages = ["ingest", "iceberg", "spark", "warehouse", "dbt", "train", "embed"]
    out = []
    for i in range(8):
        day = now - timedelta(days=i)
        rng = random.Random("run-" + day.strftime("%Y-%m-%d"))
        status = "running" if i == 0 else ("error" if rng.random() < 0.12 else "ok")
        out.append({
            "date": day.strftime("%Y-%m-%d"),
            "status": status,
            "duration_s": rng.randint(320, 900),
            "rows": rng.randint(4000, 22000),
            "stages": stages,
        })
    return out


def main():
    mode, profiles = build_profiles()
    now = datetime.now(timezone.utc)

    metrics = _model_metrics()
    stats = {
        "drugs": len(profiles),
        "adverse_reports": sum(p.get("total_reports", 0) for p in profiles),
        "research_papers": sum(p.get("total_research_papers", 0) for p in profiles),
        "data_sources": 4,
        "ml_models": len(metrics),
        "avg_auc": round(sum(m["auc"] for m in metrics) / len(metrics), 3),
        "high_risk": sum(1 for p in profiles if p["risk_label"] == "HIGH"),
    }

    payload = {
        "meta": {
            "generated_at": now.isoformat(),
            "mode": mode,
            "drug_count": len(profiles),
            "api_base_url": os.environ.get("API_BASE_URL", ""),
            "disclaimer": (
                "Educational demo — model estimates, not medical advice. When not "
                "connected to the live API, all figures shown are synthetic sample "
                "data, not real FAERS / PubMed / Reddit output."
            ),
        },
        "stats": stats,
        "profiles": profiles,
        "model_metrics": metrics,
        "feature_importance": _feature_importance(profiles),
        "reaction_leaderboard": _reaction_leaderboard(profiles),
        "research_community": _research_community(profiles),
        "source_stats": _source_stats(profiles),
        "pipeline_runs": _pipeline_runs(now),
    }

    os.makedirs(DOCS_DATA, exist_ok=True)
    out_path = os.path.join(DOCS_DATA, "dashboard.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print("export_static: wrote {} profiles + 7 aggregate datasets ({} mode) -> {}".format(
        len(profiles), mode, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
