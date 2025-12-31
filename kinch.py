import os
import json
import csv
import re
from collections import defaultdict
from supabase import create_client, Client

# --- Supabase Setup ---
SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io'
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

INPUT_JSON = "lsc_rankings_by_person.json"
OUTPUT_CSV = "lsc_kinch_rankings.csv"

EVENTS = [
    "333", "222", "444", "555", "666", "777",
    "333bf", "333fm", "333oh", "clock", "minx",
    "pyram", "skewb", "sq1", "444bf", "555bf", "333mbf"
]

USE_SINGLE_IF_BETTER = {"333bf", "333fm", "444bf", "555bf"}

def parse_333mbf(value):
    if not isinstance(value, int) or value <= 0:
        return 0.0
    s = str(value).zfill(9)
    missed = int(s[:2])
    time_sec = int(s[2:7])
    points = 99 - missed
    time_ratio = 1 - (time_sec / 3600)
    return points + time_ratio

def fetch_lsc_records():
    print("📥 Fetching LSC records from Supabase...")
    resp = supabase.table("records").select("*").execute()
    rows = resp.data or []

    values = defaultdict(dict)
    for row in rows:
        event = row.get("event_id")
        typ = row.get("type")
        value = row.get("value")
        if not all([event, typ]) or value is None:
            continue
        if event == "333mbf":
            score = parse_333mbf(value)
            values[event]["single"] = max(values[event].get("single", 0), score)
        else:
            if typ not in values[event] or value < values[event][typ]:
                values[event][typ] = value
    return values

def load_personal_values():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def compute_kinch_scores(all_people, values):
    print("🧮 Computing Kinch scores...")
    kinch = {}

    for slug, ranks in all_people.items():
        ratios = {}
        for event in EVENTS:
            avg = ranks.get(f"{event}_average", {}).get("value")
            single = ranks.get(f"{event}_single", {}).get("value")
            value_avg = values[event].get("average")
            value_single = values[event].get("single")

            if event == "333mbf":
                person_score = parse_333mbf(single)
                ratio = (person_score / value_single * 100) if person_score and value_single else 0.0
            elif event in USE_SINGLE_IF_BETTER:
                ratio_avg = (value_avg / avg * 100) if avg and value_avg else 0.0
                ratio_single = (value_single / single * 100) if single and value_single else 0.0
                ratio = max(ratio_avg, ratio_single)
            else:
                ratio = (value_avg / avg * 100) if avg and value_avg else 0.0

            ratios[event] = round(ratio, 4)

        overall = round(sum(ratios.values()) / len(EVENTS), 4)
        kinch[slug] = {"ratios": ratios, "overall": overall}

    return kinch

def export_kinch_to_csv(kinch, output_file=OUTPUT_CSV):
    print(f"💾 Writing Kinch rankings to {output_file}...")
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name"] + EVENTS + ["overall"])
        for slug, data in sorted(kinch.items(), key=lambda x: -x[1]["overall"]):
            row = [slug] + [f"{data['ratios'].get(e, 0):.4f}" for e in EVENTS] + [f"{data['overall']:.4f}"]
            writer.writerow(row)
    print("✅ Kinch CSV export complete.")

def main():
    values = fetch_lsc_records()
    all_people = load_personal_values()
    kinch = compute_kinch_scores(all_people, values)
    export_kinch_to_csv(kinch)

if __name__ == "__main__":
    main()
