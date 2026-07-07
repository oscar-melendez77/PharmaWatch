"""Personal risk-adjustment layer.

The LightGBM models score risk at the DRUG level (one training row per drug),
so they can't learn how an individual's profile changes their risk. This module
layers a transparent, rule-based adjustment on top of that base score: each
personal factor (age, sex, body weight, smoking, alcohol, polypharmacy,
pregnancy) applies an explainable multiplier, and every applied adjustment is
returned so the UI can show exactly what changed and why.

These are direction-only, population-level, EDUCATIONAL estimates — not learned
from the user's data and not medical advice. A future enhancement would retrain
on individual FAERS reports (which carry patient sex/age/weight) to make the
personalization learned rather than rule-based.

Pure Python (no deps) so it's cheap to import and easy to test.
"""

OUTCOME_KEYS = ("serious", "hospitalization", "death", "disability")


def _risk_label(serious_pct):
    if serious_pct < 30:
        return "LOW"
    if serious_pct < 70:
        return "MEDIUM"
    return "HIGH"


def _clamp(value):
    return max(0.0, min(100.0, value))


def build_factors(age=None, sex=None, weight=None, smoker=False,
                  alcohol="none", concurrent_meds=0, pregnant=False):
    return {
        "age": age,
        "sex": (sex or "").strip().lower() or None,
        "weight": weight,
        "smoker": bool(smoker),
        "alcohol": (alcohol or "none").strip().lower(),
        "concurrent_meds": int(concurrent_meds or 0),
        "pregnant": bool(pregnant),
    }


def _rules(factors):
    """Return the list of applicable adjustments, each with per-outcome
    multipliers and a plain-language rationale."""
    out = []
    age = factors.get("age")
    if age is not None and age >= 65:
        out.append({
            "factor": "Age 65+",
            "detail": "Older adults clear drugs more slowly and have higher adverse-event rates.",
            "mult": {"serious": 1.25, "hospitalization": 1.30, "death": 1.35, "disability": 1.20},
        })
    elif age is not None and age < 18:
        out.append({
            "factor": "Under 18",
            "detail": "Pediatric dosing and metabolism differ; many drugs are less studied in this group.",
            "mult": {"serious": 1.15, "hospitalization": 1.15, "death": 1.10, "disability": 1.10},
        })

    weight = factors.get("weight")
    if weight is not None and weight < 50:
        out.append({
            "factor": "Low body weight (<50 kg)",
            "detail": "A standard dose is larger relative to body mass, raising exposure.",
            "mult": {"serious": 1.10, "hospitalization": 1.10, "death": 1.10, "disability": 1.05},
        })

    if factors.get("smoker"):
        out.append({
            "factor": "Smoker",
            "detail": "Smoking alters hepatic drug metabolism (CYP1A2) and adds cardiovascular strain.",
            "mult": {"serious": 1.10, "hospitalization": 1.10, "death": 1.15, "disability": 1.05},
        })

    alcohol = factors.get("alcohol", "none")
    if alcohol == "moderate":
        out.append({
            "factor": "Moderate alcohol use",
            "detail": "Alcohol can compound sedation, liver load, and GI/bleeding effects of many drugs.",
            "mult": {"serious": 1.10, "hospitalization": 1.10, "death": 1.05, "disability": 1.05},
        })
    elif alcohol == "heavy":
        out.append({
            "factor": "Heavy alcohol use",
            "detail": "Heavy use markedly raises interaction, hepatotoxicity, and CNS-depression risk.",
            "mult": {"serious": 1.25, "hospitalization": 1.30, "death": 1.20, "disability": 1.10},
        })

    meds = factors.get("concurrent_meds", 0)
    if meds >= 5:
        out.append({
            "factor": "Polypharmacy ({} concurrent meds)".format(meds),
            "detail": "Five or more concurrent drugs sharply increases interaction risk.",
            "mult": {"serious": 1.30, "hospitalization": 1.30, "death": 1.20, "disability": 1.15},
        })
    elif meds >= 3:
        out.append({
            "factor": "Multiple medications ({})".format(meds),
            "detail": "Several concurrent drugs raise the chance of a harmful interaction.",
            "mult": {"serious": 1.15, "hospitalization": 1.15, "death": 1.10, "disability": 1.08},
        })

    if factors.get("pregnant"):
        out.append({
            "factor": "Pregnancy",
            "detail": "Many drugs carry heightened caution or teratogenic uncertainty in pregnancy.",
            "mult": {"serious": 1.30, "hospitalization": 1.15, "death": 1.10, "disability": 1.20},
        })

    if factors.get("sex") in ("female", "f", "woman"):
        out.append({
            "factor": "Female",
            "detail": "Pharmacovigilance data show women report adverse drug reactions at higher rates.",
            "mult": {"serious": 1.08, "hospitalization": 1.05, "death": 1.03, "disability": 1.05},
        })

    return out


def personalize(base_pcts, factors):
    """Apply personal adjustments to base drug-level percentages.

    base_pcts: dict with keys serious/hospitalization/death/disability (0-100).
    Returns adjusted percentages, a personalized risk label, and the list of
    applied adjustments (with the serious-outcome multiplier for display).
    """
    adjustments = _rules(factors)

    mult = {k: 1.0 for k in OUTCOME_KEYS}
    for adj in adjustments:
        for outcome, factor in adj["mult"].items():
            mult[outcome] *= factor

    adjusted = {k: round(_clamp(float(base_pcts.get(k, 0.0)) * mult[k]), 1) for k in OUTCOME_KEYS}

    return {
        "applied": bool(adjustments),
        "serious_reaction_pct": adjusted["serious"],
        "hospitalization_pct": adjusted["hospitalization"],
        "death_pct": adjusted["death"],
        "disability_pct": adjusted["disability"],
        "risk_label": _risk_label(adjusted["serious"]),
        "adjustments": [
            {
                "factor": a["factor"],
                "detail": a["detail"],
                "serious_multiplier": round(a["mult"].get("serious", 1.0), 2),
            }
            for a in adjustments
        ],
    }
