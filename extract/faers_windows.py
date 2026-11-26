import json
import os
import time
import requests
from datetime import datetime, timedelta, timezone

# 1. config
URL = "https://api.fda.gov/drug/event.json"
COUNT_LIMIT = 1000                # most reactions openFDA returns for one search
RECENT_DAYS = 365                 # the recent window is the last year, everything before it is the history window
MIN_RECENT_REPORTS = 5            # skip reactions with fewer recent reports than this
PAUSE_SECONDS = 0.3               # a free key allows 240 requests a minute
MAX_ATTEMPTS = 3                  # how many times to try a request that fails with a server error
RETRY_WAIT_SECONDS = 10           # wait before retry 1, doubles for retry 2 (10s, then 20s)
MAX_BAD_REQUESTS = 3              # this many 400 answers means the search text is wrong, stop instead of repeating it
TOTAL_TOLERANCE = 0.005           # recent plus history may differ from the full total by this share
API_KEY = os.environ.get("OPENFDA_API_KEY")
DRUGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_DIR = "data/raw"

now = datetime.now(timezone.utc)
today = now.strftime("%Y-%m-%d")
recent_start = now - timedelta(days=RECENT_DAYS)
history_end = recent_start - timedelta(days=1)
RECENT_RANGE = "[" + recent_start.strftime("%Y%m%d") + " TO " + now.strftime("%Y%m%d") + "]"
HISTORY_RANGE = "[20040101 TO " + history_end.strftime("%Y%m%d") + "]"
out_path = OUT_DIR + "/faers_windows_" + today + ".json"

# every answer is saved here as soon as it arrives, so running the script again today continues where it stopped
CACHE_PATH = "data/cache/faers_windows.jsonl"

if not API_KEY:
    print("OPENFDA_API_KEY is not set, this script needs a free key from open.fda.gov/apis/authentication")
    raise SystemExit(1)

# read the drug list: standard_name|brand_name|search_ingredient
drugs = []
with open(DRUGS_FILE) as f:
    for line in f:
        line = line.strip()
        if line == "" or line.startswith("#"):
            continue
        parts = line.split("|")
        drugs.append({"name": parts[0], "brand": parts[1], "ingredient": parts[2]})

print("[1/4] config ready,", len(drugs), "drugs, recent window", RECENT_RANGE, "history window", HISTORY_RANGE)

# 2. request
# answers older than today are dropped, these counts change every day
answers = {}
if os.path.exists(CACHE_PATH):
    with open(CACHE_PATH) as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("fetched") == today:
                answers[entry["key"]] = entry["answer"]
os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
print("      cache holds", len(answers), "answers from earlier runs today")

# five searches per drug: all reports, reports in each window, and the reaction counts of each window
jobs = []
for drug in drugs:
    drug_search = '(patient.drug.openfda.generic_name:"' + drug["ingredient"] + '" OR patient.drug.openfda.brand_name:"' + drug["brand"] + '")'
    jobs.append({"key": "all|total|" + drug["name"], "params": {"search": drug_search, "limit": 1}})
    for window, window_range in [("recent", RECENT_RANGE), ("history", HISTORY_RANGE)]:
        window_search = drug_search + " AND receivedate:" + window_range
        jobs.append({"key": window + "|total|" + drug["name"], "params": {"search": window_search, "limit": 1}})
        jobs.append({"key": window + "|reactions|" + drug["name"], "params": {"search": window_search, "count": "patient.reaction.reactionmeddrapt.exact", "limit": COUNT_LIMIT}})

print("      ", len(jobs), "searches to go through")

failed_keys = []
bad_requests = 0
new_requests = 0
done = 0

for job in jobs:
    key = job["key"]
    done = done + 1

    if key not in answers:
        params = dict(job["params"])
        params["api_key"] = API_KEY

        # try the request up to MAX_ATTEMPTS times, 200 and 404 both count as a real answer
        response = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = requests.get(URL, params=params, timeout=60)
            except requests.RequestException as error:
                print("   ", key, "-> connection error:", error)
                response = None
            new_requests = new_requests + 1
            time.sleep(PAUSE_SECONDS)

            if response is not None and response.status_code in (200, 404):
                break
            if response is not None and response.status_code == 403:
                print("STOPPED: openFDA answered 403, the API key is missing or not valid, check OPENFDA_API_KEY in this window")
                raise SystemExit(1)
            if response is not None and response.status_code == 400:
                break

            wait = RETRY_WAIT_SECONDS * (2 ** attempt)
            if response is not None:
                print("   ", key, "-> status", response.status_code, ", attempt", attempt + 1, "of", MAX_ATTEMPTS)
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(wait)

        # a 400 means the search text is wrong, repeating it cannot help
        if response is not None and response.status_code == 400:
            bad_requests = bad_requests + 1
            print("   ", key, "-> status 400, the search text is not accepted")
            if bad_requests >= MAX_BAD_REQUESTS:
                print("STOPPED:", MAX_BAD_REQUESTS, "searches were not accepted, the date window search text is probably wrong, paste this message back")
                raise SystemExit(1)

        # still no real answer after all attempts, skip it for now, running the script again retries it
        if response is None or response.status_code not in (200, 404):
            failed_keys.append(key)
            continue

        # a 404 means nothing matches the search, which is a total of 0
        answer = {"status": response.status_code, "total": 0, "terms": []}
        if response.status_code == 200:
            body = response.json()
            if "count" in job["params"]:
                answer["terms"] = body.get("results", [])
            else:
                answer["total"] = body.get("meta", {}).get("results", {}).get("total", 0)

        answers[key] = answer
        with open(CACHE_PATH, "a") as f:
            f.write(json.dumps({"key": key, "answer": answer, "fetched": today}) + "\n")

    if done % 50 == 0:
        print("      ", done, "of", len(jobs), "searches done")

# rewrite the cache with one line per answer, so it does not grow every day
with open(CACHE_PATH, "w") as f:
    for key, answer in answers.items():
        f.write(json.dumps({"key": key, "answer": answer, "fetched": today}) + "\n")

print("[2/4] request done,", new_requests, "requests made,", len(answers), "answers in total")
if len(failed_keys) > 0:
    print("      searches that failed:", len(failed_keys), "- run the script again to retry them")

# 3. parse
# one record per drug and reaction seen in the recent window, with its counts in both windows
records = []
incomplete_drugs = []
mismatched_drugs = []

for drug in drugs:
    name = drug["name"]
    needed = ["all|total|" + name, "recent|total|" + name, "history|total|" + name, "recent|reactions|" + name, "history|reactions|" + name]
    if any(key not in answers for key in needed):
        incomplete_drugs.append(name)
        continue

    all_total = answers["all|total|" + name]["total"]
    recent_total = answers["recent|total|" + name]["total"]
    history_total = answers["history|total|" + name]["total"]

    # the two windows must add up to all reports of the drug, otherwise the date search is not doing what we think
    if abs(all_total - (recent_total + history_total)) > TOTAL_TOLERANCE * all_total:
        mismatched_drugs.append(name)
        continue

    # a ^ stands for an apostrophe in the FDA data, and the same reaction can come back spelled both ways
    recent_counts = {}
    for item in answers["recent|reactions|" + name]["terms"]:
        term = item["term"].upper().replace("^", "'")
        recent_counts[term] = recent_counts.get(term, 0) + item["count"]
    history_counts = {}
    for item in answers["history|reactions|" + name]["terms"]:
        term = item["term"].upper().replace("^", "'")
        history_counts[term] = history_counts.get(term, 0) + item["count"]

    # openFDA returns at most COUNT_LIMIT reactions, one missing from a full list was reported at most as often as the smallest one in it
    history_list = answers["history|reactions|" + name]["terms"]
    history_floor = 0
    if len(history_list) >= COUNT_LIMIT:
        history_floor = min(item["count"] for item in history_list)

    for term, recent_reports in recent_counts.items():
        if recent_reports < MIN_RECENT_REPORTS:
            continue

        if term in history_counts:
            history_reports = history_counts[term]
            is_upper_bound = False
        else:
            history_reports = history_floor
            is_upper_bound = history_floor > 0

        record = {
            "drug_name": name,
            "reaction": term,
            "recent_reports": recent_reports,
            "history_reports": history_reports,
            "recent_drug_reports": recent_total,
            "history_drug_reports": history_total,
            "history_is_upper_bound": is_upper_bound,
            "recent_window_start": recent_start.strftime("%Y-%m-%d"),
            "recent_window_end": now.strftime("%Y-%m-%d"),
            "ingestion_ts": datetime.now(timezone.utc).isoformat(),
        }
        records.append(record)

print("[3/4] parse done, kept", len(records))
if len(incomplete_drugs) > 0:
    print("      drugs with missing counts:", ", ".join(incomplete_drugs))
if len(mismatched_drugs) > 0:
    print("      drugs whose windows do not add up to the total:", ", ".join(mismatched_drugs))

# 4. save
os.makedirs(OUT_DIR, exist_ok=True)
with open(out_path, "w") as f:
    json.dump(records, f, indent=2)
print("[4/4] saved", len(records), "records to", out_path)
if len(failed_keys) > 0 or len(incomplete_drugs) > 0 or len(mismatched_drugs) > 0:
    print("      NOT complete: run the script again to fill in what failed")
