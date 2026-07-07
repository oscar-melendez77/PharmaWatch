import os

import requests
import gradio as gr

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:10000")


def call_predict(drug_name, age, weight, sex="", smoker=False, alcohol="none",
                 concurrent_meds=0, pregnant=False):
    try:
        response = requests.post(
            "{}/predict".format(API_BASE_URL),
            json={
                "drug_name": drug_name,
                "age": int(age),
                "weight": float(weight),
                "sex": sex or None,
                "smoker": bool(smoker),
                "alcohol": alcohol or "none",
                "concurrent_meds": int(concurrent_meds or 0),
                "pregnant": bool(pregnant),
            },
            timeout=120,
        )
        if response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None


def call_ask(drug_name, age, weight, question):
    try:
        response = requests.post(
            "{}/ask".format(API_BASE_URL),
            json={
                "drug_name": drug_name,
                "age": int(age),
                "weight": float(weight),
                "question": question,
            },
            timeout=180,
        )
        if response.status_code != 200:
            return "Error: API returned status {}".format(response.status_code)
        return response.json().get("answer", "")
    except Exception as exc:
        return "Error: {}".format(exc)


def call_digest(drug_name, age_group, risk_label):
    try:
        response = requests.post(
            "{}/digest".format(API_BASE_URL),
            json={
                "drug_name": drug_name,
                "age_group": age_group,
                "risk_label": risk_label,
            },
            timeout=180,
        )
        if response.status_code != 200:
            return "Error: API returned status {}".format(response.status_code)
        return response.json().get("digest", "")
    except Exception as exc:
        return "Error: {}".format(exc)


def derive_age_group(age):
    try:
        age_int = int(age)
    except (TypeError, ValueError):
        return "unknown"
    if age_int < 18:
        return "0-17"
    if age_int < 35:
        return "18-34"
    if age_int < 65:
        return "35-64"
    return "65+"


def run_analysis(drug_name, age, weight, sex="", smoker=False, alcohol="none",
                 concurrent_meds=0, pregnant=False):
    result = call_predict(drug_name, age, weight, sex, smoker, alcohol, concurrent_meds, pregnant)

    if result is None:
        empty_reactions = [["N/A", 0.0]]
        empty_shap = [["N/A", 0.0]]
        empty_adj = [["—", "", 1.0]]
        return (
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            "Drug not found or API unavailable",
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            empty_reactions,
            "N/A",
            empty_shap,
            drug_name or "",
            age,
            weight,
            derive_age_group(age),
            "UNKNOWN",
            "UNKNOWN",
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            empty_adj,
        )

    serious = "{:.1f}%".format(float(result.get("serious_reaction_pct", 0.0)))
    hosp = "{:.1f}%".format(float(result.get("hospitalization_pct", 0.0)))
    death = "{:.1f}%".format(float(result.get("death_pct", 0.0)))
    disab = "{:.1f}%".format(float(result.get("disability_pct", 0.0)))

    risk_label = result.get("risk_label", "UNKNOWN")
    warning_sev = result.get("label_warning_severity", "LOW")

    top_risk_factor = result.get("top_risk_factor", "") or "N/A"
    total_papers = str(int(result.get("total_research_papers", 0)))
    community = "{:.1f}".format(float(result.get("community_concern_score", 0.0)))
    research_consensus = "{:.1f}%".format(float(result.get("research_risk_consensus", 0.0)))
    dep_rate = "{:.1f}%".format(float(result.get("dependency_mention_rate", 0.0)))
    wd_rate = "{:.1f}%".format(float(result.get("withdrawal_mention_rate", 0.0)))

    reactions_data = [
        [r.get("reaction", ""), float(r.get("frequency_pct", 0.0))]
        for r in (result.get("top_5_reactions") or [])
    ]
    if not reactions_data:
        reactions_data = [["N/A", 0.0]]

    interactions_list = result.get("known_interactions") or []
    interactions_text = "\n".join(interactions_list) if interactions_list else "None reported"

    shap_data = [
        [s.get("feature", ""), float(s.get("contribution", 0.0))]
        for s in (result.get("shap_values") or [])
    ]
    if not shap_data:
        shap_data = [["N/A", 0.0]]

    age_group = derive_age_group(age)

    p_serious = "{:.1f}%".format(float(result.get("personal_serious_pct", 0.0)))
    p_hosp = "{:.1f}%".format(float(result.get("personal_hospitalization_pct", 0.0)))
    p_death = "{:.1f}%".format(float(result.get("personal_death_pct", 0.0)))
    p_disab = "{:.1f}%".format(float(result.get("personal_disability_pct", 0.0)))
    p_risk = result.get("personal_risk_label", "UNKNOWN")

    adjustments = result.get("personal_adjustments") or []
    adj_data = [
        [a.get("factor", ""), a.get("detail", ""), a.get("serious_multiplier", 1.0)]
        for a in adjustments
    ]
    if not adj_data:
        adj_data = [["No personal factors changed the score", "", 1.0]]

    return (
        serious,
        hosp,
        death,
        disab,
        risk_label,
        warning_sev,
        top_risk_factor,
        total_papers,
        community,
        research_consensus,
        dep_rate,
        wd_rate,
        reactions_data,
        interactions_text,
        shap_data,
        drug_name,
        age,
        weight,
        age_group,
        risk_label,
        p_risk,
        p_serious,
        p_hosp,
        p_death,
        p_disab,
        adj_data,
    )


def load_digest(drug_name, age_group, risk_label):
    if not drug_name:
        return "Run Risk Analysis first to generate a digest."
    return call_digest(drug_name, age_group, risk_label)


def chat(question, history, drug_name, age, weight):
    history = history or []
    if not question or not question.strip():
        return history, ""
    if not drug_name:
        history = history + [(question, "Please run Risk Analysis first to set the drug context.")]
        return history, ""
    answer = call_ask(drug_name, age, weight, question)
    history = history + [(question, answer)]
    return history, ""


with gr.Blocks(title="PharmaWatch") as demo:
    gr.Markdown("# PharmaWatch\nDrug risk intelligence platform")

    st_drug_name = gr.State("")
    st_age = gr.State(30)
    st_weight = gr.State(70)
    st_age_group = gr.State("unknown")
    st_risk_label = gr.State("UNKNOWN")

    with gr.Tabs():
        with gr.Tab("Risk Analysis"):
            with gr.Row():
                drug_name_in = gr.Textbox(label="Drug Name", placeholder="e.g. Adderall")
                age_in = gr.Number(label="Age", value=30)
                weight_in = gr.Number(label="Weight (kg)", value=70)

            with gr.Row():
                sex_in = gr.Dropdown(
                    label="Sex", choices=["", "female", "male", "other"], value=""
                )
                alcohol_in = gr.Dropdown(
                    label="Alcohol use", choices=["none", "moderate", "heavy"], value="none"
                )
                meds_in = gr.Number(label="Other meds taken", value=0, precision=0)
                smoker_in = gr.Checkbox(label="Smoker", value=False)
                pregnant_in = gr.Checkbox(label="Pregnant", value=False)

            with gr.Row():
                analyze_btn = gr.Button("Analyze", variant="primary")

            with gr.Row():
                serious_out = gr.Label(label="Serious Reaction Risk")
                hosp_out = gr.Label(label="Hospitalization Risk")
                death_out = gr.Label(label="Death Risk")
                disab_out = gr.Label(label="Disability Risk")

            with gr.Row():
                risk_level_out = gr.Textbox(label="Overall Risk Level")
                warning_sev_out = gr.Textbox(label="Label Warning Severity")

            gr.Markdown(
                "## Personalized for your profile\n"
                "_Rule-based, educational adjustments layered on the model's drug-level "
                "score based on the factors you entered — not medical advice._"
            )
            with gr.Row():
                p_risk_out = gr.Textbox(label="Personalized Risk Level")
            with gr.Row():
                p_serious_out = gr.Label(label="Serious (for you)")
                p_hosp_out = gr.Label(label="Hospitalization (for you)")
                p_death_out = gr.Label(label="Death (for you)")
                p_disab_out = gr.Label(label="Disability (for you)")
            with gr.Row():
                adjustments_out = gr.Dataframe(
                    label="Applied personal adjustments",
                    headers=["Factor", "Why", "×Serious"],
                )

            with gr.Row():
                with gr.Column():
                    top_risk_out = gr.Textbox(label="Top Risk Factor")
                    total_papers_out = gr.Textbox(label="Total Research Papers")
                with gr.Column():
                    community_out = gr.Textbox(label="Community Concern Score")
                    research_consensus_out = gr.Textbox(label="Research Risk Consensus")

            with gr.Row():
                with gr.Column():
                    dep_rate_out = gr.Textbox(label="Dependency Mention Rate")
                with gr.Column():
                    wd_rate_out = gr.Textbox(label="Withdrawal Mention Rate")

            with gr.Row():
                reactions_out = gr.Dataframe(
                    label="Top 5 Reactions",
                    headers=["Reaction", "Frequency %"],
                )

            with gr.Row():
                interactions_out = gr.Textbox(label="Known Interactions", lines=3)

            with gr.Row():
                shap_out = gr.Dataframe(
                    label="SHAP Values - Risk Factor Contributions",
                    headers=["Feature", "Contribution"],
                )

            gr.Markdown("## Research Digest")
            digest_out = gr.Textbox(label="Research Digest", lines=8)
            digest_btn = gr.Button("Load Digest")

            analyze_btn.click(
                fn=run_analysis,
                inputs=[drug_name_in, age_in, weight_in, sex_in, smoker_in, alcohol_in, meds_in, pregnant_in],
                outputs=[
                    serious_out,
                    hosp_out,
                    death_out,
                    disab_out,
                    risk_level_out,
                    warning_sev_out,
                    top_risk_out,
                    total_papers_out,
                    community_out,
                    research_consensus_out,
                    dep_rate_out,
                    wd_rate_out,
                    reactions_out,
                    interactions_out,
                    shap_out,
                    st_drug_name,
                    st_age,
                    st_weight,
                    st_age_group,
                    st_risk_label,
                    p_risk_out,
                    p_serious_out,
                    p_hosp_out,
                    p_death_out,
                    p_disab_out,
                    adjustments_out,
                ],
            )

            digest_btn.click(
                fn=load_digest,
                inputs=[st_drug_name, st_age_group, st_risk_label],
                outputs=[digest_out],
            )

        with gr.Tab("AI Agent"):
            chatbot = gr.Chatbot(label="PharmaWatch AI Agent")
            question_in = gr.Textbox(
                label="Ask a question about this drug",
                placeholder="e.g. Is this drug safe for someone with anxiety?",
            )
            send_btn = gr.Button("Send", variant="primary")
            note_box = gr.Textbox(
                value="Run Risk Analysis first to give the agent full context",
                interactive=False,
                show_label=False,
            )

            send_btn.click(
                fn=chat,
                inputs=[question_in, chatbot, st_drug_name, st_age, st_weight],
                outputs=[chatbot, question_in],
            )


if __name__ == "__main__":
    demo.launch(share=False, server_name="0.0.0.0", server_port=7860)
