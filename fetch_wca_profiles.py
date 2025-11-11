#fetch_wca_profiles.py

import os, json, time, tempfile, re
from datetime import datetime, timedelta
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
import config

WCA_BASE = config.WCA_API_BASE
CACHE_DIR = config.WCA_CACHE_DIR
USER_AGENT = {"User-Agent": config.USER_AGENT} if isinstance(config.USER_AGENT, str) else config.USER_AGENT
START_DATE = config.START_DATE

os.makedirs(CACHE_DIR, exist_ok=True)

# Google Sheets setup
creds = service_account.Credentials.from_service_account_file(
    config.GOOGLE_CREDENTIAL_FILE,
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
)
sheets = build("sheets", "v4", credentials=creds).spreadsheets()

def read_values(spreadsheet_id, rng):
    return sheets.values().get(spreadsheetId=spreadsheet_id, range=rng).execute().get("values", [])

def name_to_slug(name):
    s = (name or "").strip().lower()
    s = re.sub(r'[^\w\s-]', '', s, flags=re.U)
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'-{2,}', '-', s)
    s = s.strip('-')
    return s or "unknown"

def cache_path_for_name(name):
    slug = name_to_slug(name)
    return os.path.join(CACHE_DIR, f"{slug}.json")

def atomic_save(path, obj):
    dirn = os.path.dirname(path)
    os.makedirs(dirn, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="tmp", dir=dirn)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except: pass

def fetch_person_results(wcaid):
    url = f"{WCA_BASE}/persons/{wcaid}/results"
    r = requests.get(url, headers=USER_AGENT, timeout=30)
    r.raise_for_status()
    return r.json()

def read_wcaids_from_master():
    rows = read_values(config.SHEETS_MASTER_ID, "WCAIDs!A1:Z999")
    if not rows: return []
    header = rows[0]
    wca_col = next((i for i, h in enumerate(header) if isinstance(h, str) and h.strip().lower() in ("wcaid", "wca id", "wca_id")), None)
    name_col = next((i for i, h in enumerate(header) if isinstance(h, str) and h.strip().lower() in ("name", "full name")), None)
    out = []
    for r in rows[1:]:
        if wca_col is not None and wca_col < len(r) and str(r[wca_col]).strip():
            wcaid = str(r[wca_col]).strip()
            name = (str(r[name_col]).strip() if (name_col is not None and name_col < len(r) and r[name_col]) else None)
            out.append({"wcaid": wcaid, "name": name})
    return out

def main():
    people = read_wcaids_from_master()
    if not people:
        print("No WCAIDs found in master sheet WCAIDs tab.")
        return
    total = len(people)
    for idx, p in enumerate(people, 1):
        wcaid = p["wcaid"]
        name = p.get("name") or wcaid
        print(f"[{idx}/{total}] {wcaid} -> {name}")
        try:
            data = fetch_person_results(wcaid)
            # normalize response to {"wcaid":..., "results":[...]}
            if isinstance(data, dict) and "results" in data:
                results = data["results"]
            elif isinstance(data, list):
                results = data
            else:
                results = data if isinstance(data, list) else []
            outobj = {"wcaid": wcaid, "results": results}
            path = cache_path_for_name(name)
            atomic_save(path, outobj)
            time.sleep(0.15)
        except Exception as e:
            print("  ERROR:", e)
    print("WCA profiles cached in", CACHE_DIR)

if __name__ == "__main__":
    main()
