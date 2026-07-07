import pandas as pd

import tools


def test_community_sentiment_text_reports_rates():
    reddit = pd.DataFrame(
        [
            {"drug_mentions": "Adderall", "body": "I feel addicted", "score": 10},
            {"drug_mentions": "Adderall", "body": "bad withdrawal quitting", "score": 30},
            {"drug_mentions": "Adderall", "body": "works fine", "score": 20},
        ]
    )
    out = tools.community_sentiment_text("Adderall", reddit)
    assert "Adderall" in out
    assert "dependency mentions" in out
    assert "withdrawal mentions" in out


def test_community_sentiment_text_no_data():
    assert "No community" in tools.community_sentiment_text("Adderall", None)
    assert "No community" in tools.community_sentiment_text("Adderall", pd.DataFrame())


def test_label_info_text_found():
    labels = pd.DataFrame(
        [
            {
                "brand_name": "ADDERALL",
                "warnings": "May cause serious cardiovascular events.",
                "interactions": "MAO inhibitors, SSRIs",
                "warnings_length": 1200,
            }
        ]
    )
    out = tools.label_info_text("Adderall", labels)
    assert "FDA label for Adderall" in out
    assert "Warning severity: MEDIUM" in out
    assert "MAO inhibitors" in out


def test_label_info_text_not_found():
    labels = pd.DataFrame([{"brand_name": "TYLENOL", "warnings": "x", "interactions": "", "warnings_length": 10}])
    assert "No FDA label found" in tools.label_info_text("Adderall", labels)


def test_label_info_text_no_data():
    assert "No FDA label data" in tools.label_info_text("Adderall", None)


def test_format_risk_summary_shape():
    out = tools.format_risk_summary({
        "serious_reaction_pct": 55.0,
        "hospitalization_pct": 20.0,
        "death_pct": 3.0,
        "disability_pct": 6.0,
        "risk_label": "MEDIUM",
    })
    assert "Serious reaction: 55.0%" in out
    assert "Risk label: MEDIUM" in out


def test_format_risk_summary_none():
    assert tools.format_risk_summary(None) == "No risk data available."
