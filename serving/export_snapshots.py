import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery

# 1. config
load_dotenv()
PROJECT_ID = os.environ["GCP_PROJECT_ID"]
BQ_LOCATION = os.environ["BQ_LOCATION"]
MARTS = "`" + PROJECT_ID + ".pharmawatch_marts."

# the API reads these files, so the public service never needs access to BigQuery
DATA_DIR = Path("serving/api/data")
ARTIFACT_DIR = Path("serving/api/artifacts")
DATA_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

QUERIES = {
    "drug_profile": "select * except (top_signals) from " + MARTS + "mart_drug_profile`",
    "safety_signals": "select * from " + MARTS + "mart_safety_signals`",
    "emerging_signals": (
        "select drug_name, reaction, recent_reports, history_reports, recent_drug_reports, history_drug_reports, "
        "recent_rate, history_rate, rate_ratio, rate_ratio_lower_95, history_is_upper_bound, is_emerging, "
        "cast(recent_window_start as string) as recent_window_start, cast(recent_window_end as string) as recent_window_end "
        "from " + MARTS + "mart_emerging_signals`"
    ),
    "monthly_trends": (
        "select drug_name, cast(month as string) as month, reports, serious_reports, hospitalized_reports, death_reports "
        "from " + MARTS + "mart_monthly_trends`"
    ),
}
print("[1/3] config ready, project", PROJECT_ID)

# 2. read each mart from BigQuery and save it as a parquet file
client = bigquery.Client(project=PROJECT_ID, location=BQ_LOCATION)
for name, sql in QUERIES.items():
    df = client.query(sql).to_dataframe()
    path = DATA_DIR / (name + ".parquet")
    df.to_parquet(path, index=False)
    print("[2/3]", name, ":", len(df), "rows ->", path, "(" + str(round(path.stat().st_size / 1e6, 2)) + " MB)")

# 3. copy the trained model files next to the API
for file_name in ["risk_model.txt", "feature_info.json", "metrics.json"]:
    shutil.copy(Path("ml/artifacts") / file_name, ARTIFACT_DIR / file_name)
print("[3/3] copied the model files to", ARTIFACT_DIR)
