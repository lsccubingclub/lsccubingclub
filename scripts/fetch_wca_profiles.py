#!/usr/bin/env python3
"""
fetch_wca_profiles.py

Fetch WCA person results and store each profile locally and in Supabase.
- Reads list of WCA IDs and names from the Supabase table `wca_ids` (columns: wcaid, name).
- For each WCA id:
  - fetches results from the WCA API,
  - writes local cache file (profiles/wca/<slug>.json),
  - upserts the JSON object into the wca_profile jsonb column of the wca_ids table.
"""

import os
import json
import time
import tempfile
import re
import sys
from datetime import datetime, timedelta

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

from supabase import create_client

import config

# --- Config (from your original script) ---
WCA_BASE = config.WCA_API_BASE
CACHE_DIR = config.WCA_CACHE_DIR
USER_AGENT = {"User-Agent": config.USER_AGENT} if isinstance(config.USER_AGENT, str) else config.USER_AGENT
START_DATE = getattr(config, "START_DATE", None)

# Supabase env
SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io'

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Ensure cache dir exists
os.makedirs(CACHE_DIR, exist_ok=True)

# Google Sheets setup (kept in case you still need it elsewhere)
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

# --- Supabase helpers (safe response handling) ---

def _resp_data(resp):
    """
    Normalize supabase-py response to Python list/dict.
    """
    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp.get("data")
    return data

def fetch_wca_list_from_supabase():
    """
    Read rows from wca_ids table and return list of dicts with keys 'wcaid' and 'name'.
    """
    try:
        resp = supabase.table("wca_ids").select("wcaid,name").execute()
    except Exception as e:
        raise RuntimeError(f"Supabase query failed: {e}")
    rows = _resp_data(resp) or []
    out = []
    for r in rows:
        wcaid = r.get("wcaid")
        name = r.get("name") or wcaid
        if wcaid:
            out.append({"wcaid": str(wcaid).strip(), "name": (name or "").strip()})
    return out

def _resp_data(resp):
    """Normalize supabase-py response to Python list/dict and raise on error."""
    # prefer attribute .data
    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp.get("data")
    # check for error fields in common shapes
    err = getattr(resp, "error", None) or (resp.get("error") if isinstance(resp, dict) else None)
    if err:
        # resp.error may be an object; stringify it
        raise RuntimeError(f"Supabase error: {err}")
    return data

def find_wca_row_by_wcaid(wcaid):
    """Return the first row dict for wcaid or None."""
    try:
        resp = supabase.table("wca_ids").select("*").eq("wcaid", wcaid).limit(1).execute()
    except Exception as e:
        raise RuntimeError(f"Supabase query failed: {e}")
    rows = _resp_data(resp) or []
    return rows[0] if rows else None

def insert_wca_row(payload):
    """Insert payload into wca_ids and return inserted row dict (or raise)."""
    try:
        resp = supabase.table("wca_ids").insert(payload).execute()
    except Exception as e:
        raise RuntimeError(f"Supabase insert failed: {e}")
    rows = _resp_data(resp) or []
    # some clients return list, some return single dict; normalize
    return rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)

def update_wca_row(row_id, payload):
    """Update row by id and return updated row dict (or raise)."""
    try:
        resp = supabase.table("wca_ids").update(payload).eq("id", row_id).execute()
    except Exception as e:
        raise RuntimeError(f"Supabase update failed: {e}")
    rows = _resp_data(resp) or []
    return rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)

# --- Main flow ---

def main():
    # Read WCA list from Supabase (instead of Google Sheets)
    try:
        people = fetch_wca_list_from_supabase()
    except Exception as e:
        print("Failed to read wca_ids from Supabase:", e, file=sys.stderr)
        return

    if not people:
        print("No WCAIDs found in Supabase table wca_ids.")
        return

    total = len(people)
    print(f"Found {total} WCAIDs in Supabase to fetch and store")

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

            # 1) save local cache (unchanged)
            path = cache_path_for_name(name)
            atomic_save(path, outobj)

            # 2) upsert into Supabase wca_ids table (store JSON in wca_profile)
            payload = {
                "wcaid": wcaid,
                "name": name,
                "wca_profile": outobj
            }

            existing = find_wca_row_by_wcaid(wcaid)
            if existing and existing.get("id"):
                updated = update_wca_row(existing["id"], payload)
                if updated:
                    print(f"  Updated Supabase row id={existing['id']}")
                else:
                    print(f"  Warning: update returned no data for wcaid {wcaid}")
            else:
                inserted = insert_wca_row(payload)
                if inserted and inserted.get("id"):
                    print(f"  Inserted Supabase row id={inserted['id']}")
                else:
                    print(f"  Warning: insert returned no data for wcaid {wcaid}")

            # polite rate limit
            time.sleep(0.15)
        except Exception as e:
            print("  ERROR:", e)

    print("WCA profiles cached locally and stored in Supabase table wca_ids")

if __name__ == "__main__":
    main()
