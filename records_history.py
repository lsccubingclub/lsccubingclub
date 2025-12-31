import os
import json
import re
import requests
from datetime import datetime
from collections import defaultdict
from supabase import create_client, Client

# --- Supabase Setup ---
SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io'
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

LSC_DIR = "profiles/lsc"

# --- Load merged profiles ---
def load_profiles():
    profiles = []
    for fn in os.listdir(LSC_DIR):
        if not fn.endswith("-merged.json"):
            continue
        path = os.path.join(LSC_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "results" in data:
                    profiles.append(data)
        except:
            continue
    return profiles

# --- Fetch LSC competition dates from Supabase ---
def fetch_lsc_dates():
    print("Fetching LSC competition dates from Supabase...")
    resp = supabase.table("competitions").select("name,date").execute()
    rows = resp.data or []
    return {r["name"]: r["date"] for r in rows if "LSC" in r.get("name", "") and r.get("date")}

# --- Fetch WCA competition name mapping ---
def fetch_wca_comp_titles():
    print("Fetching WCA competition titles from Supabase...")
    resp = supabase.table("wca_comp_titles").select("comp_id,comp_name").execute()
    rows = resp.data or []
    return {r["comp_id"]: r["comp_name"] for r in rows if r.get("comp_id") and r.get("comp_name")}

# --- Scrape WCA competition date from competition page ---
def fetch_wca_date(comp_name):
    slug = re.sub(r"[^\w]", "", comp_name)
    url = f"https://www.worldcubeassociation.org/competitions/{slug}"
    try:
        r = requests.get(url, timeout=10)
        match = re.search(r"Date\s*</strong>\s*([^<]+)", r.text)
        if match:
            raw = match.group(1).strip()
            date_str = raw.split("–")[0].strip().replace("–", "-")
            return datetime.strptime(date_str, "%b %d, %Y").strftime("%Y-%m-%d")
    except:
        pass
    return None

# --- Parse date string to datetime ---
def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except:
        return None

# --- Collect best results per event/comp/type ---
def collect_best_results(profiles, lsc_dates, wca_titles):
    singles = []
    averages = []
    date_cache = {}

    for profile in profiles:
        name = profile.get("person", {}).get("name", "Unknown")
        seen = set()

        for r in profile["results"]:
            event = r.get("event_id")
            comp = r.get("competition_id")
            if not event or not comp:
                continue

            # Determine date
            if "LSC" in comp:
                date_str = lsc_dates.get(comp)
            else:
                if comp not in date_cache:
                    comp_name = wca_titles.get(comp)
                    date_cache[comp] = fetch_wca_date(comp_name) if comp_name else None
                date_str = date_cache.get(comp)

            date = parse_date(date_str)
            if not date or date.year < 2023:
                continue

            key_base = (event, comp)
            best = r.get("best")
            avg = r.get("average")

            key_single = key_base + ("single",)
            key_avg = key_base + ("average",)

            if isinstance(best, int) and best > 0 and key_single not in seen:
                singles.append({"event": event, "value": best, "date": date, "name": name, "comp": comp})
                seen.add(key_single)

            if isinstance(avg, int) and avg > 0 and key_avg not in seen:
                averages.append({"event": event, "value": avg, "date": date, "name": name, "comp": comp})
                seen.add(key_avg)

    return singles, averages

# --- Track record progression ---
def track_progression(results):
    history = defaultdict(list)
    results.sort(key=lambda x: x["date"])
    for r in results:
        event = r["event"]
        if not history[event] or r["value"] <= history[event][-1]["value"]:
            history[event].append(r)
    return history

# --- Print record history ---
def print_history(title, history):
    print(f"\n📈 {title} Record Progressions (since 2023):")
    for event, records in sorted(history.items()):
        print(f"\nEvent: {event}")
        for r in records:
            print(f"  {r['date'].strftime('%Y-%m-%d')} | {r['value']} | {r['name']} @ {r['comp']}")

# --- Main ---
def main():
    profiles = load_profiles()
    lsc_dates = fetch_lsc_dates()
    wca_titles = fetch_wca_comp_titles()
    singles, averages = collect_best_results(profiles, lsc_dates, wca_titles)
    single_history = track_progression(singles)
    average_history = track_progression(averages)
    print_history("Single", single_history)
    print_history("Average", average_history)

if __name__ == "__main__":
    main()
