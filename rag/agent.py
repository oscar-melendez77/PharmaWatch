import os
import sys
import json
from pathlib import Path

_RAG_DIR = str(Path(__file__).resolve().parent)
_ML_DIR = str(Path(__file__).resolve().parent.parent / "ml")
for _p in (_RAG_DIR, _ML_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from langchain_groq import ChatGroq
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.tools import Tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

import retriever
from tools import (
    format_risk_summary,
    risk_scores_text,
    community_sentiment_text,
    label_info_text,
)

GROQ_MODEL = "llama-3.1-8b-instant"

SYSTEM_PROMPT = (
    "You are PharmaWatch, a drug safety intelligence assistant. You can pull from "
    "four collected data sources with your tools: model risk scores personalized to "
    "the user (get_risk_scores), PubMed research (search_research), Reddit patient "
    "sentiment (get_community_sentiment), and FDA drug labels (get_label_info). "
    "Use the tools to ground every answer in retrieved evidence — call more than one "
    "when the question spans sources. Tailor your answer to the user's profile when "
    "given. Never fabricate medical information, and always recommend consulting a "
    "healthcare provider. This is educational, not medical advice."
)


def _llm():
    return ChatGroq(api_key=os.environ["GROQ_API_KEY"], model=GROQ_MODEL)


def _format_research_results(results, drug_name):
    if not results:
        return "No relevant research papers found for {}.".format(drug_name)
    lines = []
    for r in results:
        snippet = (r.get("abstract") or "")[:300]
        lines.append(
            "- [{}] {} ({}): {}".format(
                r.get("article_id"),
                r.get("title"),
                r.get("publish_year"),
                snippet,
            )
        )
    return "\n".join(lines)


def build_agent(master_df=None, reddit_df=None, pubmed_df=None, labels_df=None, user_profile=None):
    llm = _llm()

    def search_research(input_str):
        try:
            payload = json.loads(input_str)
        except (ValueError, TypeError):
            return "Invalid input. Expected JSON with keys 'query' and 'drug_name'."
        query = payload.get("query", "")
        drug_name = payload.get("drug_name", "")
        results = retriever.retrieve(query, drug_name)
        return _format_research_results(results, drug_name)

    def get_risk_scores(drug_name):
        return risk_scores_text(
            (drug_name or "").strip(), master_df, reddit_df, pubmed_df, labels_df, user_profile
        )

    def get_community_sentiment(drug_name):
        return community_sentiment_text((drug_name or "").strip(), reddit_df)

    def get_label_info(drug_name):
        return label_info_text((drug_name or "").strip(), labels_df)

    tools = [
        Tool(
            name="get_risk_scores",
            func=get_risk_scores,
            description=(
                "Model-predicted risk scores (serious/hospitalization/death/disability) "
                "and KPIs for a drug, personalized to the user's profile. Input: drug name."
            ),
        ),
        Tool(
            name="search_research",
            func=search_research,
            description=(
                "Search PubMed research papers about a specific drug. "
                "Input: JSON string with keys query and drug_name"
            ),
        ),
        Tool(
            name="get_community_sentiment",
            func=get_community_sentiment,
            description=(
                "Reddit patient sentiment for a drug: dependency, withdrawal, and "
                "community concern signals. Input: drug name."
            ),
        ),
        Tool(
            name="get_label_info",
            func=get_label_info,
            description=(
                "FDA drug label warnings, warning severity, and known interactions. "
                "Input: drug name."
            ),
        ),
    ]

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False)


def ask(question, drug_name, user_profile, predict_risk_result,
        master_df=None, reddit_df=None, pubmed_df=None, labels_df=None):
    executor = build_agent(master_df, reddit_df, pubmed_df, labels_df, user_profile)

    risk_context = format_risk_summary(predict_risk_result)
    human_input = (
        "Drug: {drug}\n"
        "User profile: {profile}\n\n"
        "Current risk assessment:\n{risk}\n\n"
        "Question: {question}"
    ).format(
        drug=drug_name,
        profile=user_profile,
        risk=risk_context,
        question=question,
    )

    response = executor.invoke({"input": human_input})
    return response.get("output", "")


def get_research_digest(drug_name, age_group, risk_label):
    papers = retriever.retrieve_for_profile(drug_name, age_group, risk_label, n_results=5)
    if not papers:
        return "No relevant research found for {}.".format(drug_name)

    papers_text = "\n\n".join(
        "[{}] {} ({}):\n{}".format(
            p.get("article_id"),
            p.get("title"),
            p.get("publish_year"),
            (p.get("abstract") or "")[:1000],
        )
        for p in papers
    )

    prompt = (
        "Summarize these research findings about {drug} for a {age} patient with {risk} risk. "
        "Focus on safety signals, key findings, and clinical relevance. Be concise.\n\n"
        "{papers}"
    ).format(
        drug=drug_name,
        age=age_group,
        risk=risk_label,
        papers=papers_text,
    )

    llm = _llm()
    response = llm.invoke(prompt)
    return getattr(response, "content", str(response))
