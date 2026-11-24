import json
import os
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# 1. config
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
ARTICLES_PER_DRUG = 20       # newest articles to keep for each drug
PAUSE_SECONDS = 0.5          # NCBI allows about 3 requests per second without a key
MAX_ATTEMPTS = 3             # how many times to try a request that fails
RETRY_WAIT_SECONDS = 10      # wait before retry 1, doubles for retry 2 (10s, then 20s)
DRUGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_DIR = "data/raw"

# pubmed writes some months as text, we want numbers
MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}

today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
out_path = OUT_DIR + "/pubmed_" + today + ".json"

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
# for every drug: esearch finds the article ids, efetch downloads those articles as XML
raw_batches = []
failed_drugs = []

for drug in drugs:
    # both names in one query, articles must also be about adverse effects or safety
    term = '("' + drug["ingredient"] + '" OR "' + drug["brand"] + '") AND ("adverse effects" OR toxicity OR safety)'
    esearch_params = {
        "db": "pubmed",
        "term": term,
        "retmax": ARTICLES_PER_DRUG,
        "sort": "pub_date",
        "retmode": "json",
    }

    # step A: esearch, try up to MAX_ATTEMPTS times
    esearch_response = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            esearch_response = requests.get(ESEARCH_URL, params=esearch_params, timeout=60)
        except requests.RequestException as error:
            print("   ", drug["name"], "-> esearch connection error:", error)
            esearch_response = None
        time.sleep(PAUSE_SECONDS)

        if esearch_response is not None and esearch_response.status_code == 200:
            break

        wait = RETRY_WAIT_SECONDS * (2 ** attempt)
        if esearch_response is not None:
            print("   ", drug["name"], "-> esearch status", esearch_response.status_code, ", attempt", attempt + 1, "of", MAX_ATTEMPTS)
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(wait)

    if esearch_response is None or esearch_response.status_code != 200:
        failed_drugs.append(drug["name"])
        print("   ", drug["name"], "-> FAILED at esearch")
        continue

    pmids = esearch_response.json().get("esearchresult", {}).get("idlist", [])
    if len(pmids) == 0:
        print("   ", drug["name"], "-> 0 articles")
        continue

    # step B: efetch, try up to MAX_ATTEMPTS times
    efetch_params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "xml",
        "retmode": "xml",
    }

    efetch_response = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            efetch_response = requests.get(EFETCH_URL, params=efetch_params, timeout=60)
        except requests.RequestException as error:
            print("   ", drug["name"], "-> efetch connection error:", error)
            efetch_response = None
        time.sleep(PAUSE_SECONDS)

        if efetch_response is not None and efetch_response.status_code == 200:
            break

        wait = RETRY_WAIT_SECONDS * (2 ** attempt)
        if efetch_response is not None:
            print("   ", drug["name"], "-> efetch status", efetch_response.status_code, ", attempt", attempt + 1, "of", MAX_ATTEMPTS)
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(wait)

    if efetch_response is None or efetch_response.status_code != 200:
        failed_drugs.append(drug["name"])
        print("   ", drug["name"], "-> FAILED at efetch")
        continue

    raw_batches.append({"drug": drug["name"], "xml": efetch_response.content})
    print("   ", drug["name"], "->", len(pmids), "articles")

print("[2/4] request done,", len(raw_batches), "drugs with articles")
if len(failed_drugs) > 0:
    print("      drugs that failed:", ", ".join(failed_drugs))

# 3. parse
records = []
seen_keys = set()
dropped = 0

for batch in raw_batches:
    drug_name = batch["drug"]
    root = ET.fromstring(batch["xml"])

    for article_el in root.findall(".//PubmedArticle"):
        # id and title
        pmid_el = article_el.find(".//MedlineCitation/PMID")
        article_id = None
        if pmid_el is not None:
            article_id = pmid_el.text

        title_el = article_el.find(".//Article/ArticleTitle")
        title = None
        if title_el is not None:
            title = title_el.text

        # abstract can come in several parts (background, methods, results), join them
        abstract_parts = []
        for part_el in article_el.findall(".//Article/Abstract/AbstractText"):
            if part_el.text:
                abstract_parts.append(part_el.text)
        abstract = " ".join(abstract_parts).strip()

        # publication date: year is required, month defaults to 01, day is always 01
        publish_date = None
        pub_date_el = article_el.find(".//Article/Journal/JournalIssue/PubDate")
        if pub_date_el is not None:
            year = None
            month = None

            year_el = pub_date_el.find("Year")
            if year_el is not None and year_el.text:
                year = year_el.text

            month_el = pub_date_el.find("Month")
            if month_el is not None and month_el.text:
                month = month_el.text

            # some articles only have a MedlineDate like "2023 Jan-Feb"
            if year is None:
                medline_el = pub_date_el.find("MedlineDate")
                if medline_el is not None and medline_el.text:
                    first_token = medline_el.text.strip().split(" ")[0]
                    if first_token.isdigit():
                        year = first_token

            if year is not None:
                month_number = "01"
                if month is not None:
                    if month in MONTH_MAP:
                        month_number = MONTH_MAP[month]
                    elif month.isdigit():
                        month_number = month.zfill(2)
                publish_date = year + "-" + month_number + "-01"

        # one article can come up for two drugs, so a repeat means the same article and the same drug
        key = str(article_id) + "|" + drug_name

        # drop articles we cannot use
        if not article_id:
            dropped = dropped + 1
            continue
        if key in seen_keys:
            dropped = dropped + 1
            continue
        if not title:
            dropped = dropped + 1
            continue
        if abstract == "":
            dropped = dropped + 1
            continue

        seen_keys.add(key)

        record = {
            "article_id": article_id,
            "title": title,
            "abstract": abstract,
            "publish_date": publish_date,
            "drug_name": drug_name,
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
