import time
import requests
import json
from bs4 import BeautifulSoup
from supabase import create_client, Client

# --- Config ---
USER_AGENT = "Mozilla/5.0 (compatible; WCA Title Fetcher)"
RETRIES = 3
BACKOFF = 0.5
TIMEOUT = 5

# Supabase config
SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io'
SUPABASE_TABLE = "wca_comp_titles"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Helpers ---
def fetch_wca_title(comp_id, session=None):
    url = f"https://www.worldcubeassociation.org/competitions/{comp_id}"
    s = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = s.get(url, headers=headers, timeout=TIMEOUT)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.string.strip() if soup.title else ""
            return title.split(" | ")[0].strip() if " | " in title else title
        except Exception as e:
            last_err = e
            time.sleep(BACKOFF * (2 ** (attempt - 1)))
    print(f"Failed to fetch title for {comp_id}: {last_err}")
    return None

def push_to_supabase(rows):
    print(f"Clearing existing rows in '{SUPABASE_TABLE}'...")
    try:
        supabase.table(SUPABASE_TABLE).delete().neq("comp_id", "").execute()
        print("Cleared existing rows.")
    except Exception as e:
        print(f"Failed to clear table: {e}")
        return

    print(f"Pushing {len(rows)} entries to Supabase...")
    try:
        for i in range(0, len(rows), 100):
            chunk = rows[i:i+100]
            res = supabase.table(SUPABASE_TABLE).upsert(chunk).execute()
            if not res.data:
                print("Error uploading chunk.")
            else:
                print(f"Uploaded {len(chunk)} rows.")
    except Exception as e:
        print(f"Failed to push to Supabase: {e}")

# --- Main ---
def main():
    print("Fetching WCA profiles from Supabase...")
    resp = supabase.table("wca_ids").select("wca_profile").execute()
    rows = resp.data or []

    comp_ids = set()
    for row in rows:
        profile = row.get("wca_profile")
        if not profile:
            continue
        try:
            data = json.loads(profile) if isinstance(profile, str) else profile
        except Exception:
            continue
        for r in data.get("results", []):
            cid = r.get("competition_id")
            if isinstance(cid, str) and cid and " " not in cid:
                comp_ids.add(cid)

    print(f"Found {len(comp_ids)} unique competition IDs")

    session = requests.Session()
    rows_out = []

    for cid in sorted(comp_ids):
        print(f"Fetching title for {cid}...")
        name = fetch_wca_title(cid, session)
        if name:
            rows_out.append({"comp_id": cid, "comp_name": name})
        else:
            rows_out.append({"comp_id": cid, "comp_name": cid})  # fallback
        time.sleep(0.2)

    push_to_supabase(rows_out)

if __name__ == "__main__":
    main()
