"""Export drug risk profiles to docs/data/ for the GitHub Pages dashboard.

Live-capable, sample by default: if API_BASE_URL points at a running
PharmaWatch API, this pulls real /predict results; otherwise it generates
stable, realistic sample fixtures so the Pages site renders with zero infra.
The two paths emit the identical JSON shape, so going live later needs no
frontend change.

Stdlib only (urllib, no requests/pandas) so it runs in CI unchanged.

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
from datetime import datetime, timezone

DOCS_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data"
)

# representative, recognizable drugs spanning risk levels
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

REACTIONS = [
    "Nausea", "Headache", "Dizziness", "Insomnia", "Tachycardia",
    "Anxiety", "Vomiting", "Somnolence", "Fatigue", "Tremor",
]
INTERACTIONS = [
    "MAO inhibitors", "SSRIs", "Warfarin", "Alcohol",
    "Benzodiazepines", "NSAIDs", "Beta blockers",
]

# rough per-drug severity bias so the sample looks plausible, not uniform
SEVERITY_BIAS = {
    "Fentanyl": 0.85, "Oxycodone": 0.72, "Xanax": 0.6, "Adderall": 0.5,
    "Warfarin": 0.55, "Lisinopril": 0.3, "Metformin": 0.25, "Ibuprofen": 0.2,
}


def _risk_label(serious_pct):
    if serious_pct < 30:
        return "LOW"
    if serious_pct < 70:
        return "MEDIUM"
    return "HIGH"


def _sample_profile(drug):
    """Deterministic per-drug fixture (seeded by name) so re-exports are stable
    and don't churn the committed JSON."""
    name = drug["drug_name"]
    rng = random.Random(name)
    bias = SEVERITY_BIAS.get(name, 0.4)

    serious = round(min(97.0, max(3.0, bias * 100 + rng.uniform(-12, 12))), 1)
    hosp = round(min(serious, max(1.0, serious * rng.uniform(0.4, 0.75))), 1)
    death = round(max(0.2, serious * rng.uniform(0.03, 0.15)), 1)
    disability = round(max(0.5, serious * rng.uniform(0.1, 0.3)), 1)

    reactions_pool = rng.sample(REACTIONS, 5)
    freqs = sorted((rng.uniform(5, 30) for _ in range(5)), reverse=True)
    top_5 = [
        {"reaction": r, "frequency_pct": round(f, 1)}
        for r, f in zip(reactions_pool, freqs)
    ]

    shap = []
    for feat in FEATURE_COLUMNS:
        shap.append({"feature": feat, "contribution": round(rng.uniform(-1.0, 1.0) * bias, 3)})
    shap.sort(key=lambda d: abs(d["contribution"]), reverse=True)

    warning_sev = rng.choice(["LOW", "MEDIUM", "HIGH", "HIGH"] if bias > 0.5 else ["LOW", "LOW", "MEDIUM"])

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
        return json.loads(resp.read().decode("utf-8"))


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


def main():
    mode, profiles = build_profiles()
    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "drug_count": len(profiles),
            # when the pipeline runs with a live API configured, surface it so the
            # dashboard chat panel can reach /ask without manual setup
            "api_base_url": os.environ.get("API_BASE_URL", ""),
            "disclaimer": (
                "Educational demo. Risk scores are model estimates, not medical "
                "advice. Sample data is synthetic when not connected to the live API."
            ),
        },
        "profiles": profiles,
    }

    os.makedirs(DOCS_DATA, exist_ok=True)
    out_path = os.path.join(DOCS_DATA, "dashboard.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print("export_static: wrote {} profiles ({} mode) -> {}".format(len(profiles), mode, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
