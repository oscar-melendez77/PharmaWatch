import glob
import json
import os
import time
import requests
from datetime import datetime, timezone

# 1. config
URL = "https://api.fda.gov/drug/event.json"
COUNT_LIMIT = 1000                # most reactions openFDA returns for one search
TOP_REACTIONS_PER_DRUG = 1000     # keep only the most reported reactions of each drug, 1000 is everything openFDA returns
MIN_DRUG_REACTION_REPORTS = 3     # skip reactions reported fewer times than this for a drug
PAUSE_SECONDS = 0.3               # a free key allows 240 requests a minute
MAX_ATTEMPTS = 3                  # how many times to try a request that fails with a server error
RETRY_WAIT_SECONDS = 10           # wait before retry 1, doubles for retry 2 (10s, then 20s)
ZERO_STREAK_LIMIT = 10            # this many reaction totals of 0 in a row means the reaction search is not matching
REACTION_TOTAL_MAX_AGE_DAYS = 30  # a reaction's total over all of FAERS changes slowly, reuse it for this many days
API_KEY = os.environ.get("OPENFDA_API_KEY")
DRUGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_DIR = "data/raw"

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out_path = OUT_DIR + "/faers_counts_" + today + ".json"

# every answer is saved here as soon as it arrives, with the day it was fetched
# the same file is kept between days: reaction totals are reused for a while, everything else is asked again each day
CACHE_PATH = "data/cache/faers_counts.jsonl"

# this script makes thousands of requests, the keyless limit is 1000 a day
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

print("[1/4] config ready,", len(drugs), "drugs, will save to", out_path)

# 2. request
answers = {}
fetched_on = {}
today_date = datetime.strptime(today, "%Y-%m-%d")
if os.path.exists(CACHE_PATH):
    with open(CACHE_PATH) as f:
        for line in f:
            entry = json.loads(line)
            fetched = entry.get("fetched", today)
            age_days = (today_date - datetime.strptime(fetched, "%Y-%m-%d")).days

            # reaction totals can be reused for a while, anything else only on the day it was fetched
            if entry["key"].startswith("reaction|"):
                max_age = REACTION_TOTAL_MAX_AGE_DAYS
            else:
                max_age = 0

            if age_days <= max_age:
                answers[entry["key"]] = entry["answer"]
                fetched_on[entry["key"]] = fetched
os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
print("      cache holds", len(answers), "answers that can be reused")

# an older version of this script kept one cache file per day, rename it to keep its answers
old_caches = glob.glob("data/cache/faers_counts_20*.jsonl")
if len(answers) == 0 and len(old_caches) > 0:
    print("      found an old cache file", old_caches[0], "- stop and rename it to", CACHE_PATH, "to reuse its answers")

failed_keys = []
new_requests = 0

# phase 1 asks for the total of all reports and, for each drug, its report total and its reaction counts
# phase 2 asks for the total of every reaction that phase 1 found
for phase in (1, 2):
    jobs = []

    if phase == 1:
        jobs.append({"key": "all|reports", "params": {"search": "receivedate:[20040101 TO 20991231]", "limit": 1}})
        for drug in drugs:
            search = 'patient.drug.openfda.generic_name:"' + drug["ingredient"] + '" OR patient.drug.openfda.brand_name:"' + drug["brand"] + '"'
            jobs.append({"key": "drug|reports|" + drug["name"], "params": {"search": search, "limit": 1}})
            jobs.append({"key": "drug|reactions|" + drug["name"], "params": {"search": search, "count": "patient.reaction.reactionmeddrapt.exact", "limit": COUNT_LIMIT}})
    else:
        # in the FDA data an apostrophe is often stored as ^ (CROHN^S DISEASE), openFDA cannot search for that
        # so both spellings are treated as one reaction named with an apostrophe
        reaction_names = []
        seen_names = set()
        for drug in drugs:
            drug_answer = answers.get("drug|reactions|" + drug["name"])
            if drug_answer is None:
                continue
            for item in drug_answer["terms"][:TOP_REACTIONS_PER_DRUG]:
                name = item["term"].replace("^", "'")
                if item["count"] < MIN_DRUG_REACTION_REPORTS:
                    continue
                if name in seen_names or '"' in name:
                    continue
                seen_names.add(name)
                reaction_names.append(name)

        for name in reaction_names:
            if "'" in name:
                # search the words of the name, not the exact text, so it finds both spellings (an approximate total)
                words = name.replace("'", " ")
                jobs.append({"key": "reaction|phrase|" + name, "params": {"search": 'patient.reaction.reactionmeddrapt:"' + words + '"', "limit": 1}})
            else:
                jobs.append({"key": "reaction|reports|" + name, "params": {"search": 'patient.reaction.reactionmeddrapt.exact:"' + name + '"', "limit": 1}})

    print("      phase", phase, ":", len(jobs), "searches")
    done = 0
    zero_streak = 0

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
                    print("   ", key, "-> status 400, the search text is not accepted, not retrying")
                    break

                wait = RETRY_WAIT_SECONDS * (2 ** attempt)
                if response is not None:
                    print("   ", key, "-> status", response.status_code, ", attempt", attempt + 1, "of", MAX_ATTEMPTS)
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(wait)

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
            fetched_on[key] = today
            with open(CACHE_PATH, "a") as f:
                f.write(json.dumps({"key": key, "answer": answer, "fetched": today}) + "\n")

        # the most common reactions always exist, so a long run of zeros means the search text is wrong
        if phase == 2:
            if answers[key]["total"] == 0:
                zero_streak = zero_streak + 1
            else:
                zero_streak = 0
            if zero_streak >= ZERO_STREAK_LIMIT:
                print("STOPPED:", ZERO_STREAK_LIMIT, "reaction totals in a row came back 0, last one was", key)
                print("        the exact reaction search is probably not matching the terms from the count, paste this message back")
                raise SystemExit(1)

        if done % 100 == 0:
            print("      ", done, "of", len(jobs), "searches done")

# rewrite the cache with one line per answer, so it does not grow every day
with open(CACHE_PATH, "w") as f:
    for key, answer in answers.items():
        f.write(json.dumps({"key": key, "answer": answer, "fetched": fetched_on[key]}) + "\n")

print("[2/4] request done,", new_requests, "requests made,", len(answers), "answers in total")
if len(failed_keys) > 0:
    print("      searches that failed:", len(failed_keys), "- run the script again to retry them")

# 3. parse
# one record per drug and reaction, with every number the PRR formula needs
if "all|reports" not in answers:
    print("the total number of FAERS reports is missing, run the script again")
    raise SystemExit(1)
total_reports = answers["all|reports"]["total"]

# reaction totals by name, the ones found with a words search are marked as approximate
reaction_totals = {}
reaction_approximate = {}
for key, answer in answers.items():
    if key.startswith("reaction|reports|"):
        term = key[len("reaction|reports|"):].upper()
        if "'" in term or "^" in term:
            continue
        reaction_totals[term] = answer["total"]
        reaction_approximate[term] = False
    elif key.startswith("reaction|phrase|"):
        term = key[len("reaction|phrase|"):].upper()
        reaction_totals[term] = answer["total"]
        reaction_approximate[term] = True

records = []
dropped = 0
incomplete_drugs = []

for drug in drugs:
    reports_answer = answers.get("drug|reports|" + drug["name"])
    reactions_answer = answers.get("drug|reactions|" + drug["name"])
    if reports_answer is None or reactions_answer is None:
        incomplete_drugs.append(drug["name"])
        continue
    drug_reports = reports_answer["total"]

    drug_reaction_totals = {}
    for item in reactions_answer["terms"][:TOP_REACTIONS_PER_DRUG]:
        term = item["term"].upper().replace("^", "'")
        drug_reaction_totals[term] = drug_reaction_totals.get(term, 0) + item["count"]

    for term, count in drug_reaction_totals.items():
        if count < MIN_DRUG_REACTION_REPORTS:
            continue

        reaction_reports = reaction_totals.get(term)

        # drop rows with a missing reaction total or numbers that cannot be true together
        if reaction_reports is None:
            dropped = dropped + 1
            continue
        if count > drug_reports or count > reaction_reports:
            dropped = dropped + 1
            continue
        if drug_reports > total_reports or reaction_reports > total_reports:
            dropped = dropped + 1
            continue

        record = {
            "drug_name": drug["name"],
            "reaction": term,
            "drug_reaction_reports": count,
            "drug_reports": drug_reports,
            "reaction_reports": reaction_reports,
            "total_reports": total_reports,
            "reaction_total_approximate": reaction_approximate[term],
            "ingestion_ts": datetime.now(timezone.utc).isoformat(),
        }
        records.append(record)

print("[3/4] parse done, kept", len(records), "dropped", dropped)
if len(incomplete_drugs) > 0:
    print("      drugs with missing counts:", ", ".join(incomplete_drugs))

# 4. save
os.makedirs(OUT_DIR, exist_ok=True)
with open(out_path, "w") as f:
    json.dump(records, f, indent=2)
print("[4/4] saved", len(records), "records to", out_path)
if len(failed_keys) > 0 or len(incomplete_drugs) > 0:
    print("      NOT complete: run the script again to fill in what failed")
