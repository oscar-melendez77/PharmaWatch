import numpy as np
import pandas as pd

import features


def test_age_group_buckets():
    assert features.derive_age_group(10) == "0-17"
    assert features.derive_age_group(17) == "0-17"
    assert features.derive_age_group(18) == "18-34"
    assert features.derive_age_group(34) == "18-34"
    assert features.derive_age_group(50) == "35-64"
    assert features.derive_age_group(65) == "65+"
    assert features.derive_age_group(90) == "65+"


def test_age_group_unknown_on_missing():
    assert features.derive_age_group(None) == "unknown"
    assert features.derive_age_group(float("nan")) == "unknown"
    assert features.derive_age_group("not-a-number") == "unknown"


def _master_row(drug="ADDERALL", total=100):
    return {
        "drug_name": drug,
        "adv_avg_age": 40,
        "adv_serious_reports": 30,
        "adv_total_reports": total,
        "adv_hospitalization_reports": 12,
        "adv_death_reports": 3,
        "adv_disability_reports": 6,
        "sent_total_mentions": 25,
        "sent_avg_score": 4.5,
        "res_total_papers": 10,
        "lbl_warnings_length": 800,
        "lbl_has_interactions": 1,
    }


def test_build_user_profile_returns_feature_columns():
    master = pd.DataFrame([_master_row()])
    profile = features.build_user_profile("ADDERALL", 28, 70, master)
    assert profile is not None
    assert list(profile.columns) == features.FEATURE_COLUMNS
    assert profile.iloc[0]["age_group"] == "18-34"


def test_build_user_profile_missing_drug_returns_none():
    master = pd.DataFrame([_master_row()])
    assert features.build_user_profile("NOPE", 28, 70, master) is None


def test_build_features_labels_and_filtering():
    rows = [_master_row("A", total=100), _master_row("B", total=0)]
    df = pd.DataFrame(rows)
    X, y = features.build_features(df)

    # the total==0 row is filtered out before labels are built
    assert len(X) == 1
    assert list(X.columns) == features.FEATURE_COLUMNS
    # serious label = serious_reports / total_reports
    assert np.isclose(y["y_serious"][0], 30 / 100)
    assert np.isclose(y["y_death"][0], 3 / 100)
