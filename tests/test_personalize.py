import personalize

BASE = {"serious": 40.0, "hospitalization": 20.0, "death": 4.0, "disability": 8.0}


def test_neutral_profile_no_change():
    factors = personalize.build_factors(age=40, weight=70)
    out = personalize.personalize(BASE, factors)
    assert out["applied"] is False
    assert out["adjustments"] == []
    assert out["serious_reaction_pct"] == 40.0
    assert out["risk_label"] == "MEDIUM"


def test_elderly_raises_risk():
    factors = personalize.build_factors(age=72, weight=70)
    out = personalize.personalize(BASE, factors)
    assert out["applied"] is True
    assert out["serious_reaction_pct"] > BASE["serious"]
    assert any(a["factor"] == "Age 65+" for a in out["adjustments"])


def test_multipliers_stack():
    solo = personalize.personalize(BASE, personalize.build_factors(age=40, weight=70, alcohol="heavy"))
    stacked = personalize.personalize(
        BASE, personalize.build_factors(age=40, weight=70, alcohol="heavy", concurrent_meds=6)
    )
    assert stacked["serious_reaction_pct"] > solo["serious_reaction_pct"]
    assert len(stacked["adjustments"]) == 2


def test_clamped_at_100():
    high = {"serious": 90.0, "hospitalization": 80.0, "death": 50.0, "disability": 40.0}
    factors = personalize.build_factors(age=72, alcohol="heavy", concurrent_meds=8, pregnant=True)
    out = personalize.personalize(high, factors)
    assert out["serious_reaction_pct"] <= 100.0
    assert out["hospitalization_pct"] <= 100.0


def test_risk_label_can_cross_band():
    low_base = {"serious": 26.0, "hospitalization": 10.0, "death": 1.0, "disability": 2.0}
    factors = personalize.build_factors(age=72, alcohol="heavy")
    out = personalize.personalize(low_base, factors)
    # 26% * (1.25 * 1.25) = ~40% -> crosses LOW into MEDIUM
    assert out["risk_label"] == "MEDIUM"


def test_female_modifier_applies():
    out = personalize.personalize(BASE, personalize.build_factors(age=40, weight=70, sex="female"))
    assert any(a["factor"] == "Female" for a in out["adjustments"])


def test_build_factors_normalizes():
    f = personalize.build_factors(sex="Female", alcohol="HEAVY", concurrent_meds=None)
    assert f["sex"] == "female"
    assert f["alcohol"] == "heavy"
    assert f["concurrent_meds"] == 0
