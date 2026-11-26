import os

import gradio as gr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests

# 1. config
# the address of the API, set as a variable in the Space settings
API_URL = os.environ.get("API_URL", "http://localhost:8080").rstrip("/")
# the first request can wake a sleeping service, so be patient
REQUEST_TIMEOUT = 90

# the 50 drugs the platform covers
DRUGS = [
    "albuterol",
    "allopurinol",
    "amlodipine",
    "amoxicillin",
    "amphetamine",
    "apixaban",
    "atorvastatin",
    "bupropion",
    "buspirone",
    "carvedilol",
    "cetirizine",
    "clopidogrel",
    "cyclobenzaprine",
    "duloxetine",
    "empagliflozin",
    "escitalopram",
    "estradiol",
    "famotidine",
    "fluoxetine",
    "fluticasone",
    "furosemide",
    "gabapentin",
    "hydrochlorothiazide",
    "hydrocodone",
    "hydroxyzine",
    "ibuprofen",
    "insulin glargine",
    "levothyroxine",
    "lisinopril",
    "losartan",
    "meloxicam",
    "metformin",
    "methylphenidate",
    "metoprolol",
    "montelukast",
    "omeprazole",
    "ondansetron",
    "oxycodone",
    "pantoprazole",
    "prednisone",
    "rosuvastatin",
    "semaglutide",
    "sertraline",
    "simvastatin",
    "spironolactone",
    "tamsulosin",
    "tirzepatide",
    "tramadol",
    "trazodone",
    "venlafaxine",
]

SIGNAL_COLUMNS = {
    "reaction": "Reaction",
    "drug_reaction_reports": "Reports",
    "prr": "PRR",
    "prr_lower_95": "PRR lower 95%",
    "ror_lower_95": "ROR lower 95%",
}
EMERGING_COLUMNS = {
    "reaction": "Reaction",
    "recent_reports": "Reports last year",
    "history_reports": "Reports before",
    "rate_ratio": "Rate ratio",
    "rate_ratio_lower_95": "Rate ratio lower 95%",
}

INTRO = """# PharmaWatch
Drug safety signals from FDA adverse event reports (FAERS), for 50 widely used drugs.

**Educational project, not medical advice.** A signal means a reaction is reported unusually often with a drug. It does not prove the drug causes it, and reactions that match the reason a drug is prescribed score high too. Talk to a doctor or pharmacist about your medicines."""

ABOUT = """### Where the numbers come from
- **Adverse event reports:** FDA FAERS through the openFDA API. Reports are voluntary and unverified, and one event can be reported more than once.
- **Signals:** for every drug and reaction, the PRR (proportional reporting ratio) and ROR (reporting odds ratio) compare how often the reaction is reported with the drug against all other reports in FAERS. A strong signal needs at least 10 reports and a PRR lower 95% bound of at least 5. Administrative terms such as "drug ineffective" are left out.
- **Emerging:** the share of a drug's reports that mention a reaction in the last year, compared with the years before. News, lawsuits and changes in who reports can raise it.
- **Risk score:** a LightGBM model trained on the newest FAERS reports of each drug. It predicts the share of similar reports (same drug, age, weight, sex and number of medicines) that were marked serious. It is **not** a patient's personal risk. Reporting is biased towards serious cases, and age and weight are often missing from reports. The bars show how each input pushed the prediction (SHAP values).
- **Also used:** PubMed and ClinicalTrials.gov are collected for each drug and not shown here yet.

### How it is built
Python extract scripts, Google Cloud Storage and BigQuery, dbt models with tests, LightGBM with MLflow tracking, a FastAPI service on Cloud Run and this Gradio app. Infrastructure is defined with Terraform."""


# 2. helpers and handlers
def call_api(path, params=None):
    try:
        response = requests.get(API_URL + path, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException:
        raise gr.Error("The data service is not answering right now, please try again in a minute.")
    return response.json()


def number_text(value, digits=1):
    if value is None:
        return "not available"
    return str(round(value, digits))


def percent_text(value):
    if value is None:
        return "not available"
    return str(round(value * 100)) + "%"


def profile_text(profile):
    return (
        "**" + profile["drug_name"].title() + "**  \n"
        + "Reports in FAERS: " + format(int(profile["drug_reports"]), ",") + "  \n"
        + "Reactions scored: " + format(int(profile["scored_pairs"]), ",") + ", signals: " + format(int(profile["signals"]), ",")
        + ", strong signals: " + format(int(profile["strong_signals"]), ",") + "  \n"
        + "In its newest reports (" + str(profile["recent_reports"]) + "): "
        + percent_text(profile["recent_serious_share"]) + " serious, "
        + percent_text(profile["recent_hospitalized_share"]) + " hospitalization, "
        + percent_text(profile["recent_death_share"]) + " death, median age " + number_text(profile["recent_median_age_years"], 0)
    )


def signal_table(rows, columns):
    table = pd.DataFrame(rows, columns=list(columns.keys()))
    for column in table.columns:
        if table[column].dtype == float:
            table[column] = table[column].round(1)
    return table.rename(columns=columns)


def explore(drug):
    profile = call_api("/drugs/" + drug + "/profile")
    signals = call_api("/drugs/" + drug + "/signals", {"limit": 15})
    emerging = call_api("/drugs/" + drug + "/emerging", {"limit": 10})
    return profile_text(profile), signal_table(signals, SIGNAL_COLUMNS), signal_table(emerging, EMERGING_COLUMNS)


def compare(drug_a, drug_b):
    if drug_a == drug_b:
        raise gr.Error("Pick two different drugs.")
    result = call_api("/compare", {"drug_a": drug_a, "drug_b": drug_b})
    shared = pd.DataFrame(result["shared"], columns=["reaction", "drug_reaction_reports_a", "drug_reaction_reports_b", "prr_lower_95_a", "prr_lower_95_b"])
    shared = shared.round(1).rename(columns={
        "reaction": "Reaction",
        "drug_reaction_reports_a": "Reports " + drug_a,
        "drug_reaction_reports_b": "Reports " + drug_b,
        "prr_lower_95_a": "PRR lower 95% " + drug_a,
        "prr_lower_95_b": "PRR lower 95% " + drug_b,
    })
    return (
        profile_text(result["a"]), signal_table(result["a"]["top_signals"], SIGNAL_COLUMNS),
        profile_text(result["b"]), signal_table(result["b"]["top_signals"], SIGNAL_COLUMNS),
        shared,
    )


def risk(drug, age, weight, sex, n_drugs):
    params = {"drug_name": drug}
    if age is not None:
        params["age_years"] = age
    if weight is not None:
        params["weight_kg"] = weight
    if sex in ("male", "female"):
        params["sex"] = sex
    if n_drugs is not None:
        params["n_drugs"] = int(n_drugs)
    result = call_api("/risk", params)

    text = "### " + percent_text(result["share_serious"]) + " of similar reports were marked serious\n"
    if result["drug_recent_serious_share"] is not None:
        text = text + "For comparison, " + percent_text(result["drug_recent_serious_share"]) + " of the newest " + drug + " reports were serious.  \n"
    text = text + "This is a share of past reports, not your personal risk. It is not medical advice."

    names = [item["feature"].replace("patient_", "").replace("_", " ") for item in result["explanation"]]
    pushes = [item["log_odds_push"] for item in result["explanation"]]
    colors = ["tab:red" if push > 0 else "tab:blue" for push in pushes]
    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.barh(names, pushes, color=colors)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("push on the prediction (log odds), red = more serious, blue = less")
    ax.set_title("What moved the prediction")
    fig.tight_layout()
    return text, fig


# 3. the page
with gr.Blocks(title="PharmaWatch") as demo:
    gr.Markdown(INTRO)

    with gr.Tab("Drug explorer"):
        explore_drug = gr.Dropdown(choices=DRUGS, value="amphetamine", label="Drug")
        explore_button = gr.Button("Show")
        explore_summary = gr.Markdown()
        gr.Markdown("**Reactions reported unusually often** (strong signals, strongest first)")
        explore_signals = gr.Dataframe(interactive=False)
        gr.Markdown("**Reactions on the rise** (last year compared with the years before)")
        explore_emerging = gr.Dataframe(interactive=False)
        explore_button.click(explore, inputs=[explore_drug], outputs=[explore_summary, explore_signals, explore_emerging])

    with gr.Tab("Compare drugs"):
        with gr.Row():
            compare_a = gr.Dropdown(choices=DRUGS, value="amphetamine", label="Drug A")
            compare_b = gr.Dropdown(choices=DRUGS, value="methylphenidate", label="Drug B")
        compare_button = gr.Button("Compare")
        with gr.Row():
            with gr.Column():
                summary_a = gr.Markdown()
                signals_a = gr.Dataframe(interactive=False)
            with gr.Column():
                summary_b = gr.Markdown()
                signals_b = gr.Dataframe(interactive=False)
        gr.Markdown("**Reactions that stand out for both drugs**")
        shared_table = gr.Dataframe(interactive=False)
        compare_button.click(compare, inputs=[compare_a, compare_b], outputs=[summary_a, signals_a, summary_b, signals_b, shared_table])

    with gr.Tab("Risk score"):
        gr.Markdown("Leave a field empty if you do not know it.")
        risk_drug = gr.Dropdown(choices=DRUGS, value="amphetamine", label="Drug")
        with gr.Row():
            risk_age = gr.Number(label="Age (years)", value=None, minimum=0, maximum=120)
            risk_weight = gr.Number(label="Weight (kg)", value=None, minimum=1, maximum=400)
            risk_sex = gr.Radio(choices=["not given", "male", "female"], value="not given", label="Sex")
            risk_n_drugs = gr.Number(label="Number of medicines you take", value=None, minimum=1, maximum=100, precision=0)
        risk_button = gr.Button("Estimate")
        risk_text = gr.Markdown()
        risk_plot = gr.Plot()
        risk_button.click(risk, inputs=[risk_drug, risk_age, risk_weight, risk_sex, risk_n_drugs], outputs=[risk_text, risk_plot])

    with gr.Tab("How it works"):
        gr.Markdown(ABOUT)

demo.launch()
