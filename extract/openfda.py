import json
import os
import time
import requests
from datetime import datetime, timezone

# 1. config
URL = "https://api.fda.gov/drug/label.json"
LABELS_PER_SEARCH = 3            # labels to keep per search (many companies publish a label for the same drug)
SORT = "effective_time:desc"     # most recently updated labels first
TEXT_LIMIT = 500                 # keep only the first 500 characters of long text fields
PAUSE_SECONDS = 0.5              # wait between requests to be polite to the API
MAX_ATTEMPTS = 3                 # how many times to try a request that fails with a server error
RETRY_WAIT_SECONDS = 10          # wait before retry 1, doubles for retry 2 (10s, then 20s)
API_KEY = os.environ.get("OPENFDA_API_KEY")    # optional, a free key raises the daily request limit
DRUGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_DIR = "data/raw"

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out_path = OUT_DIR + "/openfda_" + today + ".json"

# finished drugs are saved here, so running the script again today skips them
CACHE_DIR = "data/cache/openfda_" + today

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
# for every drug we search twice: once by generic name, once by brand name
raw_labels = []
failed_drugs = []

for drug in drugs:
    cache_path = os.path.join(CACHE_DIR, drug["name"].replace(" ", "_") + ".json")

    # already downloaded today, load it from disk instead of asking the API again
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cached_labels = json.load(f)
        for label in cached_labels:
            raw_labels.append({"drug": drug["name"], "label": label})
        print("   ", drug["name"], "->", len(cached_labels), "labels (from cache)")
        continue

    searches = [
        'openfda.generic_name:"' + drug["ingredient"] + '"',
        'openfda.brand_name:"' + drug["brand"] + '"',
    ]
    drug_labels = []
    drug_failed = False

    for search in searches:
        params = {"search": search, "limit": LABELS_PER_SEARCH, "sort": SORT}
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

        # still no real answer after all attempts, skip this search and mark the drug as failed
        if response is None or response.status_code not in (200, 404):
            drug_failed = True
            continue

        # a 404 means no label matches this search
        if response.status_code == 404:
            continue

        for label in response.json().get("results", []):
            drug_labels.append(label)

    for label in drug_labels:
        raw_labels.append({"drug": drug["name"], "label": label})

    if drug_failed:
        failed_drugs.append(drug["name"])
        print("   ", drug["name"], "-> FAILED, kept", len(drug_labels), "labels, run again to retry")
    else:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(drug_labels, f)
        print("   ", drug["name"], "->", len(drug_labels), "labels")

print("[2/4] request done,", len(raw_labels), "raw labels received")
if len(failed_drugs) > 0:
    print("      drugs that failed:", ", ".join(failed_drugs))

# 3. parse
records = []
seen_keys = set()
dropped = 0

for item in raw_labels:
    drug_name = item["drug"]
    label = item["label"]

    label_id = label.get("id")
    openfda = label.get("openfda")

    # a repeat means the same label found by both the generic and the brand search
    key = str(label_id) + "|" + drug_name

    # drop labels with no id or no openfda block
    if not label_id:
        dropped = dropped + 1
        continue
    if key in seen_keys:
        dropped = dropped + 1
        continue
    if openfda is None:
        dropped = dropped + 1
        continue

    seen_keys.add(key)

    # brand and generic names are lists, take the first one
    brand_name = "unknown"
    brand_list = openfda.get("brand_name")
    if isinstance(brand_list, list) and len(brand_list) > 0:
        brand_name = brand_list[0]

    generic_name = "unknown"
    generic_list = openfda.get("generic_name")
    if isinstance(generic_list, list) and len(generic_list) > 0:
        generic_name = generic_list[0]

    # warnings text can be a list or a plain string
    warnings_text = None
    warnings_value = label.get("warnings")
    if isinstance(warnings_value, list) and len(warnings_value) > 0:
        warnings_text = warnings_value[0][:TEXT_LIMIT]
    elif isinstance(warnings_value, str):
        warnings_text = warnings_value[:TEXT_LIMIT]

    # drug interactions text, same shape as warnings
    interactions_text = None
    interactions_value = label.get("drug_interactions")
    if isinstance(interactions_value, list) and len(interactions_value) > 0:
        interactions_text = interactions_value[0][:TEXT_LIMIT]
    elif isinstance(interactions_value, str):
        interactions_text = interactions_value[:TEXT_LIMIT]

    record = {
        "drug_id": label_id,
        "drug_name": drug_name,
        "brand_name": brand_name,
        "generic_name": generic_name,
        "warnings": warnings_text,
        "interactions": interactions_text,
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
