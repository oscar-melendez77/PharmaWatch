import json
import os
import time
import requests
from datetime import datetime, timezone

# 1. config
URL = "https://clinicaltrials.gov/api/v2/studies"
STUDIES_PER_SEARCH = 5       # studies to keep for each search
PAUSE_SECONDS = 1            # wait between requests to be polite to the API
DRUGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_DIR = "data/raw"

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out_path = OUT_DIR + "/clinicaltrials_" + today + ".json"

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
# for every drug we search twice: once by ingredient, once by brand name, only studies with posted results
raw_studies = []

for drug in drugs:
    search_terms = [drug["ingredient"], drug["brand"]]
    drug_total = 0

    for term in search_terms:
        params = {
            "query.intr": term,
            "filter.advanced": "AREA[HasResults]true",
            "pageSize": STUDIES_PER_SEARCH,
            "format": "json",
        }
        try:
            response = requests.get(URL, params=params, timeout=60)
            response.raise_for_status()
            studies = response.json().get("studies", [])
            for study in studies:
                raw_studies.append({"drug": drug["name"], "study": study})
            drug_total = drug_total + len(studies)
        except requests.RequestException as error:
            print("   request failed for", term, ":", error)
        time.sleep(PAUSE_SECONDS)

    print("   ", drug["name"], "->", drug_total, "studies")

print("[2/4] requests done,", len(raw_studies), "studies received in total")

# 3. parse
records = []
seen_keys = set()
dropped = 0

for item in raw_studies:
    drug_name = item["drug"]
    study = item["study"]

    protocol = study.get("protocolSection") or {}
    results = study.get("resultsSection") or {}

    identification = protocol.get("identificationModule") or {}
    status_module = protocol.get("statusModule") or {}
    design_module = protocol.get("designModule") or {}
    adverse_module = results.get("adverseEventsModule") or {}

    nct_id = identification.get("nctId")
    title = identification.get("briefTitle")
    status = status_module.get("overallStatus")

    start_date_block = status_module.get("startDateStruct") or {}
    start_date = start_date_block.get("date")

    phases = design_module.get("phases") or []
    phase = ",".join(phases)

    enrollment_block = design_module.get("enrollmentInfo") or {}
    enrollment = enrollment_block.get("count")

    # a repeat means the same study found by both the ingredient and the brand search
    key = str(nct_id) + "|" + drug_name

    if not nct_id:
        dropped = dropped + 1
        continue
    if key in seen_keys:
        dropped = dropped + 1
        continue

    seen_keys.add(key)

    # adverse events: each term has one stats entry per trial group, add the groups together
    adverse_events = []
    for kind in ["seriousEvents", "otherEvents"]:
        for event in adverse_module.get(kind) or []:
            affected_total = 0
            at_risk_total = 0
            for stat in event.get("stats") or []:
                try:
                    affected_total = affected_total + int(stat.get("numAffected"))
                except (TypeError, ValueError):
                    affected_total = affected_total + 0
                try:
                    at_risk_total = at_risk_total + int(stat.get("numAtRisk"))
                except (TypeError, ValueError):
                    at_risk_total = at_risk_total + 0

            adverse_event = {
                "term": event.get("term"),
                "is_serious": kind == "seriousEvents",
                "num_affected": affected_total,
                "num_at_risk": at_risk_total,
            }
            adverse_events.append(adverse_event)

    record = {
        "nct_id": nct_id,
        "title": title,
        "drug": drug_name,
        "status": status,
        "phase": phase,
        "enrollment": enrollment,
        "start_date": start_date,
        "adverse_events": adverse_events,
        "ingestion_ts": datetime.now(timezone.utc).isoformat(),
    }
    records.append(record)

print("[3/4] parse done, kept", len(records), "dropped", dropped)

# 4. save
os.makedirs(OUT_DIR, exist_ok=True)
with open(out_path, "w") as f:
    json.dump(records, f, indent=2)
print("[4/4] saved", len(records), "records to", out_path)
