"""Lightweight demo API — the easy path to a working chat.

The full api/main.py needs the warehouse, MLflow models, and ChromaDB to boot.
This demo app needs none of that: it serves /predict from the sample profiles in
docs/data/dashboard.json (applying the real personalization layer) and answers
/ask by sending the drug's risk context to Groq. The only requirement is a free
GROQ_API_KEY. Great for a public chat demo without provisioning any infra.

Run locally:
    pip install -r api/requirements-demo.txt
    GROQ_API_KEY=... uvicorn api.demo_app:app --host 0.0.0.0 --port 8000
"""
import json
import os
import sys
from pathlib import Path

_API_DIR = str(Path(__file__).resolve().parent)
_ML_DIR = str(Path(__file__).resolve().parent.parent / "ml")
for _p in (_API_DIR, _ML_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import PredictRequest, AskRequest
from personalize import build_factors, personalize

DASHBOARD = Path(__file__).resolve().parent.parent / "docs" / "data" / "dashboard.json"
GROQ_MODEL = "llama3-8b-8192"

SYSTEM_PROMPT = (
    "You are PharmaWatch, a drug safety intelligence assistant. Answer using ONLY "
    "the risk data provided in the user's message (model risk scores, KPIs, top "
    "reactions, interactions). Tailor the answer to the user's profile. Be concise, "
    "never invent numbers, and always end by recommending they consult a healthcare "
    "provider. This is an educational demo, not medical advice."
)

_PROFILES = {}


def _load_profiles():
    global _PROFILES
    data = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    _PROFILES = {p["drug_name"].upper(): p for p in data.get("profiles", [])}
    return _PROFILES


app = FastAPI(title="PharmaWatch Demo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_load_profiles()


def _profile(name):
    return _PROFILES.get((name or "").strip().upper())


def _personalized(profile, req):
    factors = build_factors(
        age=req.age, sex=req.sex, weight=req.weight, smoker=req.smoker,
        alcohol=req.alcohol, concurrent_meds=req.concurrent_meds, pregnant=req.pregnant,
    )
    return personalize(
        {
            "serious": profile["serious_reaction_pct"],
            "hospitalization": profile["hospitalization_pct"],
            "death": profile["death_pct"],
            "disability": profile["disability_pct"],
        },
        factors,
    )


@app.get("/health")
def health():
    return {"status": "ok", "mode": "demo", "drugs": len(_PROFILES)}


@app.post("/predict")
def predict(req: PredictRequest):
    profile = _profile(req.drug_name)
    if profile is None:
        raise HTTPException(status_code=404, detail="Drug not in demo set: {}".format(req.drug_name))
    per = _personalized(profile, req)
    out = dict(profile)
    out.update({
        "personalized": per["applied"],
        "personal_serious_pct": per["serious_reaction_pct"],
        "personal_hospitalization_pct": per["hospitalization_pct"],
        "personal_death_pct": per["death_pct"],
        "personal_disability_pct": per["disability_pct"],
        "personal_risk_label": per["risk_label"],
        "personal_adjustments": per["adjustments"],
    })
    return out


def _risk_context(profile, per):
    reactions = ", ".join(
        "{} {:.0f}%".format(r["reaction"], r["frequency_pct"]) for r in profile.get("top_5_reactions", [])
    )
    interactions = ", ".join(profile.get("known_interactions", [])) or "none listed"
    lines = [
        "Serious reaction: {:.1f}%".format(profile["serious_reaction_pct"]),
        "Hospitalization: {:.1f}%".format(profile["hospitalization_pct"]),
        "Death: {:.1f}%".format(profile["death_pct"]),
        "Disability: {:.1f}%".format(profile["disability_pct"]),
        "Overall risk label: {}".format(profile["risk_label"]),
        "Label warning severity: {}".format(profile.get("label_warning_severity", "UNKNOWN")),
        "Community concern score: {:.0f}/100".format(profile.get("community_concern_score", 0)),
        "Research papers: {}".format(profile.get("total_research_papers", 0)),
        "Top reactions: {}".format(reactions or "n/a"),
        "Known interactions: {}".format(interactions),
    ]
    if per["applied"]:
        lines.append("Personalized serious risk: {:.1f}% ({})".format(
            per["serious_reaction_pct"], per["risk_label"]))
        for a in per["adjustments"]:
            lines.append("  - {}: {}".format(a["factor"], a["detail"]))
    return "\n".join(lines)


@app.post("/ask")
def ask(req: AskRequest):
    profile = _profile(req.drug_name)
    if profile is None:
        raise HTTPException(status_code=404, detail="Drug not in demo set: {}".format(req.drug_name))

    per = _personalized(profile, req)
    context = _risk_context(profile, per)
    user_msg = (
        "Drug: {drug}\n"
        "User profile: age {age}, sex {sex}, alcohol {alc}, smoker {smoke}, "
        "other meds {meds}, pregnant {preg}\n\n"
        "Risk data:\n{ctx}\n\n"
        "Question: {q}"
    ).format(
        drug=req.drug_name, age=req.age, sex=req.sex or "n/a", alc=req.alcohol,
        smoke=req.smoker, meds=req.concurrent_meds, preg=req.pregnant,
        ctx=context, q=req.question,
    )

    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
        )
        return {"answer": resp.choices[0].message.content}
    except KeyError:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not set on the server.")
    except Exception as exc:  # surface a readable error to the chat box
        raise HTTPException(status_code=502, detail="LLM call failed: {}".format(exc))
