import json
import os
import time
import requests
from datetime import datetime, timezone

# 1. config
URL = "https://api.fda.gov/drug/event.json"
FIRST_YEAR = 2018                 # the first year of the monthly history, every extra year adds 200 searches
COUNT_LIMIT = 1000                # most days openFDA returns for one search, a year has at most 366
PAUSE_SECONDS = 0.3               # a free key allows 240 requests a minute
MAX_ATTEMPTS = 3                  # how many times to try a request that fails with a server error
RETRY_WAIT_SECONDS = 10           # wait before retry 1, doubles for retry 2 (10s, then 20s)
MAX_BAD_REQUESTS = 3              # this many 400 answers means the search text is wrong, stop instead of repeating it
API_KEY = os.environ.get("OPENFDA_API_KEY")
DRUGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_DIR = "data/raw"

now = datetime.now(timezone.utc)
today = now.strftime("%Y-%m-%d")
years = list(range(FIRST_YEAR, now.year + 1))
out_path = OUT_DIR + "/faers_monthly_" + today + ".json"

# every answer is saved here as soon as it arrives, so running the script again today continues where it stopped
CACHE_PATH = "data/cache/faers_monthly.jsonl"

# each metric is the same search plus one extra condition, serious:1 means the report was marked serious
METRICS = {
    "reports": "",
    "serious_reports": " AND serious:1",
    "hospitalized_reports": " AND seriousnesshospitalization:1",
    "death_reports": " AND seriousnessdeath:1",
}

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

print("[1/4] config ready,", len(drugs), "drugs, years", years[0], "to", years[-1])

# 2. request
# answers older than today are dropped, a new FAERS release can change old months
answers = {}
if os.path.exists(CACHE_PATH):
    with open(CACHE_PATH) as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("fetched") == today:
                answers[entry["key"]] = entry["answer"]
os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
print("      cache holds", len(answers), "answers from earlier runs today")

# one search per drug, year and metric, openFDA answers with the number of reports for each day of that year
jobs = []
for drug in drugs:
    drug_search = '(patient.drug.openfda.generic_name:"' + drug["ingredient"] + '" OR patient.drug.openfda.brand_name:"' + drug["brand"] + '")'
    for year in years:
        year_range = "[" + str(year) + "0101 TO " + str(year) + "1231]"
        for metric, extra in METRICS.items():
            jobs.append({
                "key": metric + "|" + str(year) + "|" + drug["name"],
                "params": {"search": drug_search + " AND receivedate:" + year_range + extra, "count": "receivedate", "limit": COUNT_LIMIT},
            })

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
                print("STOPPED: openFDA answered 403, the API key is missing or not valid, check OPENFDA_API_KEY")
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
                print("STOPPED:", MAX_BAD_REQUESTS, "searches were not accepted, the search text is probably wrong, paste this message back")
                raise SystemExit(1)

        # still no real answer after all attempts, skip it for now, running the script again retries it
        if response is None or response.status_code not in (200, 404):
            failed_keys.append(key)
            continue

        # a 404 means nothing matches the search, which is no reports that year
        answer = {"status": response.status_code, "days": []}
        if response.status_code == 200:
            answer["days"] = response.json().get("results", [])

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
# add the days of each year up into months, one record per drug and month
records = []
incomplete_drugs = []
odd_months = 0

for drug in drugs:
    name = drug["name"]
    needed = [metric + "|" + str(year) + "|" + name for metric in METRICS for year in years]
    if any(key not in answers for key in needed):
        incomplete_drugs.append(name)
        continue

    months = {}
    for metric in METRICS:
        for year in years:
            for item in answers[metric + "|" + str(year) + "|" + name]["days"]:
                # openFDA names the date of a count "time", the text of a count "term"
                day = item.get("time") or item.get("term")
                month = day[0:4] + "-" + day[4:6] + "-01"
                if month not in months:
                    months[month] = {"reports": 0, "serious_reports": 0, "hospitalized_reports": 0, "death_reports": 0}
                months[month][metric] = months[month][metric] + item["count"]

    for month in sorted(months):
        counts = months[month]
        if counts["serious_reports"] > counts["reports"]:
            odd_months = odd_months + 1
        records.append({
            "drug_name": name,
            "month": month,
            "reports": counts["reports"],
            "serious_reports": counts["serious_reports"],
            "hospitalized_reports": counts["hospitalized_reports"],
            "death_reports": counts["death_reports"],
            "ingestion_ts": datetime.now(timezone.utc).isoformat(),
        })

print("[3/4] parse done, kept", len(records), "drug months")
if len(incomplete_drugs) > 0:
    print("      drugs with missing counts:", ", ".join(incomplete_drugs))
if odd_months > 0:
    print("      months with more serious reports than reports:", odd_months)

# 4. save
os.makedirs(OUT_DIR, exist_ok=True)
with open(out_path, "w") as f:
    json.dump(records, f, indent=2)
print("[4/4] saved", len(records), "records to", out_path)
if len(failed_keys) > 0 or len(incomplete_drugs) > 0:
    print("      NOT complete: run the script again to fill in what failed")
