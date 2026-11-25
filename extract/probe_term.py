import os
import time
import requests

# 1. config
URL = "https://api.fda.gov/drug/event.json"
API_KEY = os.environ.get("OPENFDA_API_KEY")
PAUSE_SECONDS = 1

if not API_KEY:
    print("OPENFDA_API_KEY is not set in this window")
    raise SystemExit(1)

# each line is a label and the search text to try
searches = [
    ("caret, as returned by the count", 'patient.reaction.reactionmeddrapt.exact:"CROHN^S DISEASE"'),
    ("caret with a backslash", 'patient.reaction.reactionmeddrapt.exact:"CROHN\\^S DISEASE"'),
    ("apostrophe spelling, exact", 'patient.reaction.reactionmeddrapt.exact:"CROHN\'S DISEASE"'),
    ("words only, not exact", 'patient.reaction.reactionmeddrapt:"CROHN S DISEASE"'),
    ("slash in the name", 'patient.reaction.reactionmeddrapt.exact:"ATTENTION DEFICIT/HYPERACTIVITY DISORDER"'),
]

# 2. request
for label, search in searches:
    params = {"search": search, "limit": 1, "api_key": API_KEY}
    response = requests.get(URL, params=params, timeout=60)
    time.sleep(PAUSE_SECONDS)

    # 3. parse
    total = None
    message = ""
    if response.status_code == 200:
        total = response.json().get("meta", {}).get("results", {}).get("total")
    else:
        message = response.text[:150].replace("\n", " ")

    # 4. show
    print(label, "-> status", response.status_code, "| total", total, "|", message)
