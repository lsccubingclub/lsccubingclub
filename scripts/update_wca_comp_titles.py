import os
import json
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path

# --- Config ---
WCA_PROFILES_DIR = "profiles/wca"
TITLE_CACHE_PATH = "wca_comp_titles.json"
USER_AGENT = "Mozilla/5.0 (compatible; WCA Title Fetcher)"
RETRIES = 3
BACKOFF = 0.5
TIMEOUT = 5

# --- Helpers ---
def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def load_json_if_exists(path):
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def atomic_write(path, data):
    tmp_path = str(path) + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)

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

# --- Main ---
def main():
    print("Scanning WCA profiles for competition IDs...")
    comp_ids = set()
    for fn in os.listdir(WCA_PROFILES_DIR):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(WCA_PROFILES_DIR, fn)
        data = load_json_if_exists(path)
        if not data:
            continue
        for r in data.get("results", []):
            cid = r.get("competition_id")
            if isinstance(cid, str) and cid and " " not in cid:
                comp_ids.add(cid)

    print(f"Found {len(comp_ids)} unique competition IDs")

    cache = load_json_if_exists(TITLE_CACHE_PATH) or {}
    session = requests.Session()
    updated = False

    for cid in sorted(comp_ids):
        if cid in cache:
            continue
        print(f"Fetching title for {cid}...")
        name = fetch_wca_title(cid, session)
        if name:
            cache[cid] = name
        else:
            cache[cid] = cid  # fallback
        updated = True
        time.sleep(0.2)

    if updated:
        ensure_dir(os.path.dirname(TITLE_CACHE_PATH))
        atomic_write(TITLE_CACHE_PATH, cache)
        print(f"Updated {TITLE_CACHE_PATH} with {len(cache)} entries.")
    else:
        print("No new titles needed updating.")

if __name__ == "__main__":
    main()
