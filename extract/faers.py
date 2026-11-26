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
MAX_ATTEMPTS = 3             # how many times to try a request that fails with a server error
RETRY_WAIT_SECONDS = 10      # wait before retry 1, doubles for retry 2 (10s, then 20s)
# FAERS reports age with a unit code, this is how many years one unit of each code is
AGE_UNIT_TO_YEARS = {"800": 10.0, "801": 1.0, "802": 1 / 12, "803": 1 / 52.1775, "804": 1 / 365.25, "805": 1 / 8766}
API_KEY = os.environ.get("OPENFDA_API_KEY")    # optional, a free key raises the daily request limit
DRUGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_DIR = "data/raw"

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out_path = OUT_DIR + "/faers_" + today + ".json"

# finished drugs are saved here, so running the script again today skips them
CACHE_DIR = "data/cache/faers_" + today

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
failed_drugs = []

for drug in drugs:
    cache_path = os.path.join(CACHE_DIR, drug["name"].replace(" ", "_") + ".json")

    # already downloaded today, load it from disk instead of asking the API again
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached_reports = json.load(f)
        for report in cached_reports:
            raw_reports.append({"drug": drug["name"], "report": report})
        print("   ", drug["name"], "->", len(cached_reports), "reports (from cache)")
        continue

    searches = [
        'patient.drug.openfda.generic_name:"' + drug["ingredient"] + '"',
        'patient.drug.openfda.brand_name:"' + drug["brand"] + '"',
    ]
    drug_reports = []
    drug_failed = False

    for search in searches:
        for page in range(MAX_PAGES):
            params = {"search": search, "limit": PAGE_SIZE, "skip": page * PAGE_SIZE, "sort": SORT}
            if API_KEY:
                params["api_key"] = API_KEY

            # try the request up to MAX_ATTEMPTS times, 200 and 404 both count as a real answer
            response = None
            for attempt in range(MAX_ATTEMPTS):
                try:
                    response = requests.get(URL, params=params, timeout=60)
                except requests.RequestException as error:
                    print("   ", drug["name"], "-> connection error:", error)
                    response = None
                time.sleep(PAUSE_SECONDS)

                if response is not None and response.status_code in (200, 404):
                    break

                wait = RETRY_WAIT_SECONDS * (2 ** attempt)
                if response is not None:
                    print("   ", drug["name"], "-> status", response.status_code, ", attempt", attempt + 1, "of", MAX_ATTEMPTS)
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(wait)

            # still no real answer after all attempts, give up on this drug for now
            if response is None or response.status_code not in (200, 404):
                drug_failed = True
                break

            # a 404 means no reports match this search (or no more pages)
            if response.status_code == 404:
                break

            page_reports = response.json().get("results", [])
            for report in page_reports:
                drug_reports.append(report)

            # fewer reports than a full page means this was the last page
            if len(page_reports) < PAGE_SIZE:
                break

    for report in drug_reports:
        raw_reports.append({"drug": drug["name"], "report": report})

    if drug_failed:
        failed_drugs.append(drug["name"])
        print("   ", drug["name"], "-> FAILED, kept", len(drug_reports), "reports, run again to retry")
    else:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(drug_reports, f)
        print("   ", drug["name"], "->", len(drug_reports), "reports")

print("[2/4] request done,", len(raw_reports), "raw reports received")
if len(failed_drugs) > 0:
    print("      drugs that failed:", ", ".join(failed_drugs))

# 3. parse
# one record per report, drug and reaction
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

    # sex code (1 male, 2 female, 0 unknown) and how many drugs the report lists in total
    sex_value = patient.get("patientsex")
    patient_sex = None
    if sex_value is not None:
        patient_sex = str(sex_value)
    n_drugs = len(drugs_on_report)

    # every distinct reaction on the report, a report with none still keeps one row
    reaction_terms = []
    for reaction_item in reactions:
        term = reaction_item.get("reactionmeddrapt")
        if term and term not in reaction_terms:
            reaction_terms.append(term)
    if len(reaction_terms) == 0:
        reaction_terms = [None]

    # age comes with a unit code, convert it to years, leave it empty if the unit is missing or unknown
    age_unit = patient.get("patientonsetageunit")
    age_years = None
    try:
        age_value = float(patient.get("patientonsetage"))
        if str(age_unit) in AGE_UNIT_TO_YEARS:
            age_years = round(age_value * AGE_UNIT_TO_YEARS[str(age_unit)], 4)
    except (TypeError, ValueError):
        age_years = None

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

    # drop reports we cannot use
    if not report_id:
        dropped = dropped + 1
        continue
    if severity not in (1, 2, "1", "2"):
        dropped = dropped + 1
        continue

    for reaction in reaction_terms:
        # one report can belong to two drugs, so a repeat means the same report, drug and reaction
        key = str(report_id) + "|" + drug_name + "|" + str(reaction)
        if key in seen_keys:
            dropped = dropped + 1
            continue
        seen_keys.add(key)

        record = {
            "report_id": report_id,
            "drug_name": drug_name,
            "reported_drug": reported_drug,
            "reaction": reaction,
            "severity": severity,
            "age_years": age_years,
            "age_unit": age_unit,
            "weight": weight,
            "patient_sex": patient_sex,
            "n_drugs": n_drugs,
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
if len(failed_drugs) > 0:
    print("      NOT complete:", len(failed_drugs), "drugs failed, run the script again to fill them in")
