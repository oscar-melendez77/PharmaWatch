import json
import os
import time
import requests
from datetime import datetime, timezone

# 1. config
URL = "https://api.fda.gov/drug/event.json"
PAGE_SIZE = 100              # reports per request (without an api key openFDA allows up to 100)
MAX_PAGES = 2                # pages to fetch for each search, 2 pages x 100 = up to 200 reports
SORT = "receivedate:desc"    # newest reports first
PAUSE_SECONDS = 0.5          # wait between requests to be polite to the API
API_KEY = os.environ.get("OPENFDA_API_KEY")    # optional, a free key raises the daily request limit
DRUGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_DIR = "data/raw"

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out_path = OUT_DIR + "/faers_" + today + ".json"

# read the drug list: standard_name|brand_name|search_ingredient
drugs = []
with open(DRUGS_FILE) as f:
    for line in f:
        line = line.strip()
        if line == "" or line.startswith("#"):
            continue
        parts = line.split("|")
        drugs.append({"name": parts[0], "brand": parts[1], "ingredient": parts[2]})

print("[1/4] config ready,", len(drugs), "drugs, will save to", out_path)

# 2. request
# for every drug we search twice: once by the generic ingredient, once by the brand name
raw_reports = []

for drug in drugs:
    searches = [
        'patient.drug.openfda.generic_name:"' + drug["ingredient"] + '"',
        'patient.drug.openfda.brand_name:"' + drug["brand"] + '"',
    ]
    drug_total = 0

    for search in searches:
        for page in range(MAX_PAGES):
            params = {"search": search, "limit": PAGE_SIZE, "skip": page * PAGE_SIZE, "sort": SORT}
            if API_KEY:
                params["api_key"] = API_KEY

            try:
                response = requests.get(URL, params=params, timeout=60)
            except requests.RequestException as error:
                print("   request failed for", drug["name"], ":", error)
                break
            time.sleep(PAUSE_SECONDS)

            # a 404 means no reports match this search (or no more pages)
            if response.status_code == 404:
                break
            if response.status_code != 200:
                print("   ", drug["name"], "-> status", response.status_code, response.text[:120])
                break

            payload = response.json()
            page_reports = payload.get("results", [])
            for report in page_reports:
                raw_reports.append({"drug": drug["name"], "report": report})
            drug_total = drug_total + len(page_reports)

            # fewer reports than a full page means this was the last page
            if len(page_reports) < PAGE_SIZE:
                break

    print("   ", drug["name"], "->", drug_total, "reports")

print("[2/4] request done,", len(raw_reports), "raw reports received")

# 3. parse
records = []
seen_keys = set()
dropped = 0

for item in raw_reports:
    drug_name = item["drug"]
    report = item["report"]

    patient = report.get("patient") or {}
    drugs_on_report = patient.get("drug") or []
    reactions = patient.get("reaction") or []

    # the first drug listed on the report, kept for reference
    reported_drug = None
    if len(drugs_on_report) > 0:
        reported_drug = drugs_on_report[0].get("medicinalproduct")

    reaction = None
    if len(reactions) > 0:
        reaction = reactions[0].get("reactionmeddrapt")

    # age and weight can be missing or not numeric
    age = None
    try:
        age = int(patient.get("patientonsetage"))
    except (TypeError, ValueError):
        age = None

    weight = None
    try:
        weight = float(patient.get("patientweight"))
    except (TypeError, ValueError):
        weight = None

    # outcome flags: 1 if the report says yes, otherwise 0
    flags = {}
    for field in ["seriousnesshospitalization", "seriousnessdeath", "seriousnessdisabling"]:
        flags[field] = 0
        try:
            if int(report.get(field)) == 1:
                flags[field] = 1
        except (TypeError, ValueError):
            flags[field] = 0

    report_id = report.get("safetyreportid")
    severity = report.get("serious")

    # one report can belong to two drugs, so a repeat means the same report and the same drug
    key = str(report_id) + "|" + drug_name

    # drop reports we cannot use
    if not report_id:
        dropped = dropped + 1
        continue
    if key in seen_keys:
        dropped = dropped + 1
        continue
    if severity not in (1, 2, "1", "2"):
        dropped = dropped + 1
        continue

    seen_keys.add(key)

    record = {
        "report_id": report_id,
        "drug_name": drug_name,
        "reported_drug": reported_drug,
        "reaction": reaction,
        "severity": severity,
        "age": age,
        "weight": weight,
        "report_date": report.get("receivedate"),
        "hospitalization": flags["seriousnesshospitalization"],
        "death": flags["seriousnessdeath"],
        "disability": flags["seriousnessdisabling"],
        "ingestion_ts": datetime.now(timezone.utc).isoformat(),
    }
    records.append(record)

print("[3/4] parse done, kept", len(records), "dropped", dropped)

# 4. save
os.makedirs(OUT_DIR, exist_ok=True)
with open(out_path, "w") as f:
    json.dump(records, f, indent=2)
print("[4/4] saved", len(records), "records to", out_path)
