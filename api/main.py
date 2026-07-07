import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

_DEFAULT_ROOT = str(Path(__file__).resolve().parent.parent)
PHARMAWATCH_ROOT = os.environ.get("PHARMAWATCH_ROOT", _DEFAULT_ROOT)

for _sub in ("ml", "rag"):
    _p = os.path.join(PHARMAWATCH_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import (
    PredictRequest,
    AskRequest,
    DigestRequest,
    PredictResponse,
    AskResponse,
    DigestResponse,
    HealthResponse,
)
import dependencies
from dependencies import AppState, get_app_state, load_app_state


@asynccontextmanager
async def lifespan(app: FastAPI):
    dependencies.app_state = load_app_state()
    yield
    dependencies.app_state = None


app = FastAPI(title="PharmaWatch API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        warehouse=os.environ.get("WAREHOUSE", "unknown"),
    )


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest, state: AppState = Depends(get_app_state)):
    from predict import predict_risk
    from personalize import build_factors, personalize

    result = predict_risk(
        req.drug_name,
        req.age,
        req.weight,
        state.master_df,
        state.reddit_df,
        state.pubmed_df,
        state.labels_df,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Drug not found: {}".format(req.drug_name))

    factors = build_factors(
        age=req.age,
        sex=req.sex,
        weight=req.weight,
        smoker=req.smoker,
        alcohol=req.alcohol,
        concurrent_meds=req.concurrent_meds,
        pregnant=req.pregnant,
    )
    personal = personalize(
        {
            "serious": result.get("serious_reaction_pct", 0.0),
            "hospitalization": result.get("hospitalization_pct", 0.0),
            "death": result.get("death_pct", 0.0),
            "disability": result.get("disability_pct", 0.0),
        },
        factors,
    )

    return PredictResponse(
        drug_name=req.drug_name,
        serious_reaction_pct=float(result.get("serious_reaction_pct", 0.0)),
        hospitalization_pct=float(result.get("hospitalization_pct", 0.0)),
        death_pct=float(result.get("death_pct", 0.0)),
        disability_pct=float(result.get("disability_pct", 0.0)),
        risk_label=str(result.get("risk_label", "UNKNOWN")),
        dependency_mention_rate=float(result.get("dependency_mention_rate", 0.0)),
        withdrawal_mention_rate=float(result.get("withdrawal_mention_rate", 0.0)),
        community_concern_score=float(result.get("community_concern_score", 0.0)),
        research_risk_consensus=float(result.get("research_risk_consensus", 0.0)),
        total_research_papers=int(result.get("total_research_papers", 0)),
        top_5_reactions=result.get("top_5_reactions", []),
        label_warning_severity=str(result.get("label_warning_severity", "LOW")),
        known_interactions=result.get("known_interactions", []),
        shap_values=result.get("shap_values", []),
        top_risk_factor=str(result.get("top_risk_factor") or ""),
        personalized=personal["applied"],
        personal_serious_pct=personal["serious_reaction_pct"],
        personal_hospitalization_pct=personal["hospitalization_pct"],
        personal_death_pct=personal["death_pct"],
        personal_disability_pct=personal["disability_pct"],
        personal_risk_label=personal["risk_label"],
        personal_adjustments=personal["adjustments"],
    )


@app.post("/ask", response_model=AskResponse)
def ask_endpoint(req: AskRequest, state: AppState = Depends(get_app_state)):
    from predict import predict_risk
    from agent import ask

    risk_result = predict_risk(
        req.drug_name,
        req.age,
        req.weight,
        state.master_df,
        state.reddit_df,
        state.pubmed_df,
        state.labels_df,
    )
    if risk_result is None:
        raise HTTPException(status_code=404, detail="Drug not found: {}".format(req.drug_name))

    user_profile = {"age": req.age, "weight": req.weight}
    answer = ask(req.question, req.drug_name, user_profile, risk_result)
    return AskResponse(answer=answer)


@app.post("/digest", response_model=DigestResponse)
def digest_endpoint(req: DigestRequest):
    from agent import get_research_digest

    digest_text = get_research_digest(req.drug_name, req.age_group, req.risk_label)
    return DigestResponse(digest=digest_text)
