import gradio as gr
import pandas as pd
from fastapi import HTTPException
from matplotlib.figure import Figure

import main

# 1. config
# the dashboard runs inside the API service, so it calls the API functions directly
DRUGS = sorted(main.DRUG_NAMES)

INTRO = """# Drug Safety Intelligence
What is reported about the most used drugs in the US, built on millions of FDA adverse event reports (FAERS).
Compare drugs, see what is on the rise, and test a what-if profile.

*Educational portfolio project, not medical advice. A report is not proof that a drug caused a reaction.*"""

METHODOLOGY = """### How to read the numbers
- **Reports** are individual FDA adverse event reports. They are voluntary and unverified, so they show what gets reported, not how often something truly happens.
- **Times more common than other drugs** compares how often a reaction appears in this drug's reports against all other reports. 10x means ten times as often. The technical name is the proportional reporting ratio (PRR).
- **Evidence** says how solid that comparison is. It uses the cautious end of the estimate, so a rare reaction with a few reports cannot rank high by luck. Very strong is a cautious value of 20x or more, Strong is 10x or more, Notable is 5x or more. Every row needs at least 10 reports.
- **Rising** compares the last 12 months with the average year before, measured as a share of the drug's own reports, so a drug that simply gets more reports overall does not look worse.
- **Serious, hospitalized and death** shares come from each drug's newest reports. Reports lean towards serious cases, so these shares are higher than in real life.
- **Volume vs yearly average** divides the last 12 months by the average year since 2004. FAERS grows over time, so values above 1x are common.
- **Severity simulator:** a LightGBM model estimates what share of similar reports (same drug, age, weight, sex and number of medicines) were marked serious. It describes past reports and is not a personal risk.

### How the data gets here
FDA openFDA API, then Python extracts into Google Cloud Storage and BigQuery, then dbt models with automated tests, then a FastAPI service and this dashboard, both running on Cloud Run. LightGBM training is tracked with MLflow and the infrastructure is defined with Terraform. Administrative terms such as "drug ineffective" are filtered out of the rankings."""


# 2. helpers
def call_api(function, *args):
    try:
        return function(*args)
    except HTTPException as error:
        raise gr.Error(str(error.detail))


def integer_text(value):
    if value is None:
        return "n/a"
    return format(int(value), ",")


def percent_text(value):
    if value is None:
        return "n/a"
    return str(round(value * 100)) + "%"


def multiple_text(value):
    if value is None:
        return "n/a"
    return str(round(value, 1)) + "x"


def evidence_label(lower_bound):
    if lower_bound >= 20:
        return "Very strong"
    if lower_bound >= 10:
        return "Strong"
    return "Notable"


def kpi_cards(items):
    cards = ""
    for label, value, note in items:
        cards = cards + (
            '<div style="flex:1;min-width:150px;padding:14px 16px;border:1px solid rgba(128,128,128,0.35);border-radius:10px;">'
            '<div style="font-size:12px;opacity:0.7;">' + label + '</div>'
            '<div style="font-size:26px;font-weight:700;">' + value + '</div>'
            '<div style="font-size:12px;opacity:0.7;">' + note + '</div></div>'
        )
    return '<div style="display:flex;gap:12px;flex-wrap:wrap;">' + cards + '</div>'


def bar_chart(labels, values, title, xlabel):
    fig = Figure(figsize=(6, 3.6))
    ax = fig.subplots()
    ax.barh(labels[::-1], values[::-1], color="tab:blue")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    fig.tight_layout()
    return fig


def unusual_table(rows):
    return pd.DataFrame({
        "Reaction": [row["reaction"].title() for row in rows],
        "Reports": [row["drug_reaction_reports"] for row in rows],
        "Times more common than other drugs": [round(row["prr"], 1) for row in rows],
        "Evidence": [evidence_label(row["prr_lower_95"]) for row in rows],
    })


def rising_table(rows):
    ratios = []
    averages = []
    for row in rows:
        ratio_text = str(round(row["rate_ratio"], 1)) + "x"
        average_text = str(round(row["history_per_year"], 1))
        if row["history_is_upper_bound"]:
            ratio_text = "at least " + ratio_text
            average_text = "up to " + average_text
        ratios.append(ratio_text)
        averages.append(average_text)
    return pd.DataFrame({
        "Reaction": [row["reaction"].title() for row in rows],
        "Reports, last 12 months": [row["recent_reports"] for row in rows],
        "Average year before": averages,
        "Times its usual rate": ratios,
    })


def drug_cards(profile):
    return kpi_cards([
        ("Reports, all time", integer_text(profile["drug_reports"]), "FDA adverse event reports"),
        ("Last 12 months", integer_text(profile["recent_drug_reports"]), multiple_text(profile["volume_vs_average"]) + " the yearly average"),
        ("Serious", percent_text(profile["recent_serious_share"]), "of its newest " + integer_text(profile["recent_reports"]) + " reports"),
        ("Hospitalized", percent_text(profile["recent_hospitalized_share"]), "of its newest reports"),
        ("Deaths", percent_text(profile["recent_death_share"]), "of its newest reports"),
        ("Median age", "n/a" if profile["recent_median_age_years"] is None else str(round(profile["recent_median_age_years"])), "in its newest reports"),
    ])


# 3. handlers
def overview_view():
    data = call_api(main.overview)
    table = pd.DataFrame(data["drugs_table"])

    share = data["reports_on_tracked_drugs"] / data["total_faers_reports"]
    cards = kpi_cards([
        ("Reports in FAERS", integer_text(data["total_faers_reports"]), "all drugs, FDA database"),
        ("Drugs tracked", str(data["drugs"]), "widely prescribed in the US"),
        ("Reports on tracked drugs", integer_text(data["reports_on_tracked_drugs"]), percent_text(share) + " of all reports"),
        ("Last 12 months", integer_text(data["reports_last_12_months"]), "new reports through " + data["data_through"]),
    ])

    top_volume = table.head(10)
    fig_volume = bar_chart(top_volume["drug_name"].str.title().tolist(), top_volume["drug_reports"].tolist(), "Most reported drugs", "Reports, all time")

    top_serious = table.dropna(subset=["recent_serious_share"]).sort_values("recent_serious_share", ascending=False).head(10)
    fig_serious = bar_chart(top_serious["drug_name"].str.title().tolist(), (top_serious["recent_serious_share"] * 100).round(0).tolist(), "Highest share of serious reports", "% of newest reports marked serious")

    leaderboard = pd.DataFrame({
        "Drug": table["drug_name"].str.title(),
        "Reports (all time)": table["drug_reports"],
        "Last 12 months": table["recent_drug_reports"].astype("Int64"),
        "Volume vs yearly average (x)": table["volume_vs_average"].round(1),
        "Serious (%)": (table["recent_serious_share"] * 100).round(0).astype("Int64"),
        "Hospitalized (%)": (table["recent_hospitalized_share"] * 100).round(0).astype("Int64"),
        "Death (%)": (table["recent_death_share"] * 100).round(0).astype("Int64"),
        "Strong signals": table["strong_signals"],
    })
    return cards, fig_volume, fig_serious, leaderboard


def deep_dive(drug):
    profile = call_api(main.drug_profile, drug)
    top = pd.DataFrame(call_api(main.drug_top_reactions, drug, 10))
    unusual = call_api(main.drug_signals, drug, 10, True, 10, "prr_lower_95")
    rising = call_api(main.drug_emerging, drug, 10, True)

    fig = bar_chart(top["reaction"].str.title().tolist(), (top["share_of_drug_reports"] * 100).round(1).tolist(), "What gets reported most", "% of this drug's reports that mention it")
    return drug_cards(profile), fig, unusual_table(unusual), rising_table(rising)


def compare(drug_a, drug_b):
    if drug_a == drug_b:
        raise gr.Error("Pick two different drugs.")
    result = call_api(main.compare, drug_a, drug_b)
    a = result["a"]
    b = result["b"]

    metrics = [
        ("Reports, all time", integer_text(a["drug_reports"]), integer_text(b["drug_reports"])),
        ("Reports, last 12 months", integer_text(a["recent_drug_reports"]), integer_text(b["recent_drug_reports"])),
        ("Volume vs yearly average", multiple_text(a["volume_vs_average"]), multiple_text(b["volume_vs_average"])),
        ("Serious, newest reports", percent_text(a["recent_serious_share"]), percent_text(b["recent_serious_share"])),
        ("Hospitalized, newest reports", percent_text(a["recent_hospitalized_share"]), percent_text(b["recent_hospitalized_share"])),
        ("Deaths, newest reports", percent_text(a["recent_death_share"]), percent_text(b["recent_death_share"])),
        ("Strong signals", integer_text(a["strong_signals"]), integer_text(b["strong_signals"])),
    ]
    metric_table = pd.DataFrame(metrics, columns=["Metric", drug_a.title(), drug_b.title()])

    names = ["Serious", "Hospitalized", "Deaths"]
    values_a = [(a[key] or 0) * 100 for key in ["recent_serious_share", "recent_hospitalized_share", "recent_death_share"]]
    values_b = [(b[key] or 0) * 100 for key in ["recent_serious_share", "recent_hospitalized_share", "recent_death_share"]]
    positions = [0, 1, 2]
    fig = Figure(figsize=(6, 3.4))
    ax = fig.subplots()
    ax.bar([position - 0.2 for position in positions], values_a, 0.4, label=drug_a.title())
    ax.bar([position + 0.2 for position in positions], values_b, 0.4, label=drug_b.title())
    ax.set_xticks(positions)
    ax.set_xticklabels(names)
    ax.set_ylabel("% of newest reports")
    ax.set_title("Outcomes in the newest reports")
    ax.legend()
    fig.tight_layout()

    shared = pd.DataFrame(result["shared"], columns=["reaction", "drug_reaction_reports_a", "drug_reaction_reports_b", "weaker_score"])
    shared_table = pd.DataFrame({
        "Reaction": shared["reaction"].str.title(),
        "Reports " + drug_a.title(): shared["drug_reaction_reports_a"],
        "Reports " + drug_b.title(): shared["drug_reaction_reports_b"],
        "Evidence (weaker of the two)": [evidence_label(score) for score in shared["weaker_score"]],
    })
    return metric_table, fig, shared_table


def risk(drug, age, weight, sex, n_drugs):
    if age is not None and not 0 <= age <= 120:
        raise gr.Error("Age must be between 0 and 120.")
    if weight is not None and not 0 < weight <= 400:
        raise gr.Error("Weight must be between 1 and 400 kg.")
    if n_drugs is not None and not 1 <= n_drugs <= 100:
        raise gr.Error("Number of medicines must be between 1 and 100.")
    sex_value = sex if sex in ("male", "female") else None
    n_drugs_value = None if n_drugs is None else int(n_drugs)
    result = call_api(main.risk, drug, age, weight, sex_value, n_drugs_value)

    text = "### " + percent_text(result["share_serious"]) + " of similar reports were marked serious\n"
    if result["drug_recent_serious_share"] is not None:
        text = text + "Across the newest " + drug.title() + " reports, " + percent_text(result["drug_recent_serious_share"]) + " were serious.  \n"
    text = text + "This is a share of past reports, not a personal risk, and not medical advice."

    nice_names = {"drug_name": "Drug", "patient_sex": "Sex", "patient_age_years": "Age", "patient_weight_kg": "Weight", "n_drugs": "Number of medicines"}
    names = [nice_names[item["feature"]] for item in result["explanation"]]
    pushes = [item["log_odds_push"] for item in result["explanation"]]
    colors = ["tab:red" if push > 0 else "tab:blue" for push in pushes]
    fig = Figure(figsize=(6, 3.2))
    ax = fig.subplots()
    ax.barh(names[::-1], pushes[::-1], color=colors[::-1])
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_xlabel("effect on the estimate: red pushes towards serious, blue away")
    ax.set_title("What moved the estimate")
    fig.tight_layout()
    return text, fig


# 4. the page
with gr.Blocks(title="Drug Safety Intelligence") as demo:
    gr.Markdown(INTRO)

    with gr.Tab("Overview"):
        overview_cards = gr.HTML()
        with gr.Row():
            overview_volume = gr.Plot()
            overview_serious = gr.Plot()
        gr.Markdown("**All tracked drugs** (click a column to sort)")
        overview_table = gr.Dataframe(interactive=False)

    with gr.Tab("Drug deep dive"):
        dive_drug = gr.Dropdown(choices=DRUGS, value="amphetamine", label="Drug")
        dive_button = gr.Button("Show")
        dive_cards = gr.HTML()
        dive_chart = gr.Plot()
        gr.Markdown("**Reactions that are unusually common for this drug** compared with other drugs")
        dive_unusual = gr.Dataframe(interactive=False)
        gr.Markdown("**Reactions on the rise**, last 12 months compared with the average year before")
        dive_rising = gr.Dataframe(interactive=False)
        dive_outputs = [dive_cards, dive_chart, dive_unusual, dive_rising]
        dive_button.click(deep_dive, inputs=[dive_drug], outputs=dive_outputs)

    with gr.Tab("Compare drugs"):
        with gr.Row():
            compare_a = gr.Dropdown(choices=DRUGS, value="amphetamine", label="Drug A")
            compare_b = gr.Dropdown(choices=DRUGS, value="methylphenidate", label="Drug B")
        compare_button = gr.Button("Compare")
        compare_metrics = gr.Dataframe(interactive=False)
        compare_chart = gr.Plot()
        gr.Markdown("**Reactions that stand out for both drugs**")
        compare_shared = gr.Dataframe(interactive=False)
        compare_button.click(compare, inputs=[compare_a, compare_b], outputs=[compare_metrics, compare_chart, compare_shared])

    with gr.Tab("Severity simulator"):
        gr.Markdown("Pick a profile and see what share of similar past reports were marked serious. Leave a field empty if you do not know it.")
        risk_drug = gr.Dropdown(choices=DRUGS, value="amphetamine", label="Drug")
        with gr.Row():
            risk_age = gr.Number(label="Age (years)", value=None, minimum=0, maximum=120)
            risk_weight = gr.Number(label="Weight (kg)", value=None, minimum=1, maximum=400)
            risk_sex = gr.Radio(choices=["not given", "male", "female"], value="not given", label="Sex")
            risk_n_drugs = gr.Number(label="Number of medicines", value=None, minimum=1, maximum=100, precision=0)
        risk_button = gr.Button("Estimate")
        risk_text = gr.Markdown()
        risk_plot = gr.Plot()
        risk_button.click(risk, inputs=[risk_drug, risk_age, risk_weight, risk_sex, risk_n_drugs], outputs=[risk_text, risk_plot])

    with gr.Tab("Methodology"):
        gr.Markdown(METHODOLOGY)

    demo.load(overview_view, inputs=None, outputs=[overview_cards, overview_volume, overview_serious, overview_table])
    demo.load(deep_dive, inputs=[dive_drug], outputs=dive_outputs)
