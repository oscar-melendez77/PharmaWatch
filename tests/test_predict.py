import json

import pandas as pd

import predict


def test_risk_label_thresholds():
    assert predict._risk_label(0) == "LOW"
    assert predict._risk_label(29.9) == "LOW"
    assert predict._risk_label(30) == "MEDIUM"
    assert predict._risk_label(69.9) == "MEDIUM"
    assert predict._risk_label(70) == "HIGH"
    assert predict._risk_label(99) == "HIGH"


def test_label_severity_bands():
    assert predict._label_severity(None) == "LOW"
    assert predict._label_severity(float("nan")) == "LOW"
    assert predict._label_severity(100) == "LOW"
    assert predict._label_severity(499) == "LOW"
    assert predict._label_severity(500) == "MEDIUM"
    assert predict._label_severity(1999) == "MEDIUM"
    assert predict._label_severity(2000) == "HIGH"


def test_split_interactions():
    assert predict._split_interactions("Alcohol, SSRIs ,Warfarin") == ["Alcohol", "SSRIs", "Warfarin"]
    assert predict._split_interactions("") == []
    assert predict._split_interactions(None) == []


def test_contains_any_case_insensitive():
    assert predict._contains_any("I got ADDICTED fast", ["addicted"]) is True
    assert predict._contains_any("nothing here", ["addicted"]) is False
    assert predict._contains_any(None, ["x"]) is False


def test_reddit_kpis_rates():
    df = pd.DataFrame(
        [
            {"drug_mentions": "Adderall", "body": "I feel addicted to this", "score": 10},
            {"drug_mentions": "Adderall", "body": "bad withdrawal after quitting", "score": 30},
            {"drug_mentions": "Adderall", "body": "works fine", "score": 20},
            {"drug_mentions": "Tylenol", "body": "unrelated addicted", "score": 5},
        ]
    )
    dep_rate, wd_rate, concern = predict._reddit_kpis("Adderall", df)
    # 1 of 3 Adderall posts mentions dependency, 1 of 3 mentions withdrawal
    assert round(dep_rate, 1) == round(100 / 3, 1)
    assert round(wd_rate, 1) == round(100 / 3, 1)
    assert 0.0 <= concern <= 100.0


def test_reddit_kpis_empty():
    assert predict._reddit_kpis("X", pd.DataFrame()) == (0.0, 0.0, 0.0)


def test_pubmed_kpis_consensus():
    df = pd.DataFrame(
        [
            {"drug_name": "ADDERALL", "abstract": "serious adverse risk found"},
            {"drug_name": "ADDERALL", "abstract": "no issues at all"},
            {"drug_name": "OTHER", "abstract": "toxic danger"},
        ]
    )
    consensus, total = predict._pubmed_kpis("Adderall", df)
    assert total == 2
    assert consensus == 50.0  # 1 of 2 abstracts has a negative term


def test_top_reactions_from_json():
    row = pd.Series({"adv_top_reactions": json.dumps(
        [{"reaction": "Nausea", "frequency_pct": 20.0},
         {"reaction": "Headache", "frequency_pct": 10.0}]
    )})
    out = predict._top_reactions(row)
    assert out[0]["reaction"] == "Nausea"
    assert len(out) == 2


def test_top_reactions_fallback_to_most_common():
    row = pd.Series({"adv_most_common_reaction": "Dizziness"})
    out = predict._top_reactions(row)
    assert out == [{"reaction": "Dizziness", "frequency_pct": 100.0}]
