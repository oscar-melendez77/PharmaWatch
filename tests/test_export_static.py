import export_static

REQUIRED_KEYS = {
    "drug_name", "serious_reaction_pct", "hospitalization_pct", "death_pct",
    "disability_pct", "risk_label", "dependency_mention_rate",
    "withdrawal_mention_rate", "community_concern_score",
    "research_risk_consensus", "total_research_papers", "top_5_reactions",
    "label_warning_severity", "known_interactions", "shap_values", "top_risk_factor",
}


def test_risk_label_matches_predict_thresholds():
    assert export_static._risk_label(10) == "LOW"
    assert export_static._risk_label(50) == "MEDIUM"
    assert export_static._risk_label(80) == "HIGH"


def test_sample_profile_shape():
    p = export_static._sample_profile({"drug_name": "Adderall", "age": 28, "weight": 70})
    assert REQUIRED_KEYS.issubset(p.keys())
    assert len(p["top_5_reactions"]) == 5
    assert p["shap_values"][0]["feature"] == p["top_risk_factor"]
    assert p["risk_label"] in {"LOW", "MEDIUM", "HIGH"}


def test_sample_profile_is_deterministic():
    drug = {"drug_name": "Xanax", "age": 34, "weight": 65}
    a = export_static._sample_profile(drug)
    b = export_static._sample_profile(drug)
    assert a == b  # seeded by drug name -> stable, no churn on re-export


def test_sample_severity_ordering():
    high = export_static._sample_profile({"drug_name": "Fentanyl", "age": 50, "weight": 75})
    low = export_static._sample_profile({"drug_name": "Ibuprofen", "age": 30, "weight": 70})
    assert high["serious_reaction_pct"] > low["serious_reaction_pct"]


def test_build_profiles_sample_mode(monkeypatch):
    monkeypatch.delenv("API_BASE_URL", raising=False)
    mode, profiles = export_static.build_profiles()
    assert mode == "sample"
    assert len(profiles) == len(export_static.DRUGS)
    # sorted descending by serious reaction risk
    serious = [p["serious_reaction_pct"] for p in profiles]
    assert serious == sorted(serious, reverse=True)
