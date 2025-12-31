import os
import json
import re
import csv
from collections import defaultdict
from supabase import create_client, Client

# --- Supabase Setup ---
SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io'
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

OUTPUT_JSON = "lsc_rankings_by_person.json"
OUTPUT_CSV = "llscr_sor.csv"

def fetch_rankings():
    print("Fetching rankings_by_person from Supabase...")
    resp = supabase.table("rankings_by_person").select("*").execute()
    rows = resp.data or []

    rankings_by_name = defaultdict(dict)
    max_ranks = defaultdict(int)

    for row in rows:
        name = row.get("person_name")
        event = row.get("event_id")
        typ = row.get("type")
        rank = row.get("rank")
        value = row.get("value")
        if not all([name, event, typ]) or rank is None:
            continue
        key = f"{event}_{typ}"
        rankings_by_name[name][key] = {"rank": rank, "value": value}
        max_ranks[key] = max(max_ranks[key], rank)

    return rankings_by_name, max_ranks

def build_full_rankings(rankings_by_name, max_ranks):
    print("Building full LSC rankings for each person...")
    all_people = {}

    for name in rankings_by_name:
        full = {}
        for key, max_rank in max_ranks.items():
            if key in rankings_by_name[name]:
                full[key] = rankings_by_name[name][key]
            else:
                full[key] = {"rank": max_rank + 1, "value": None}
        all_people[name] = full

    return all_people

def compute_sum_of_ranks(all_people):
    print("Computing LLSCR SOR...")
    sum_single = {}
    sum_avg = {}

    for name, ranks in all_people.items():
        total_single = sum(v["rank"] for k, v in ranks.items() if k.endswith("_single"))
        total_avg = sum(v["rank"] for k, v in ranks.items() if k.endswith("_average"))
        sum_single[name] = total_single
        sum_avg[name] = total_avg

    return sum_single, sum_avg

def export_sum_of_ranks_to_csv(sum_single, sum_avg, output_file=OUTPUT_CSV):
    print(f"💾 Writing CSV to {output_file}...")
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "total_single_rank", "total_average_rank"])
        for name in sorted(sum_single.keys()):
            writer.writerow([name, sum_single[name], sum_avg.get(name, "")])
    print("CSV export complete.")

def print_rankings(title, rank_dict):
    print(f"\n {title} LLSCR SOR")
    for i, (name, total) in enumerate(sorted(rank_dict.items(), key=lambda x: x[1])):
        print(f"{i+1:>2}. {name}: {total}")

EVENT_ORDER = [
    "333", "222", "444", "555", "666", "777",
    "333bf", "333fm", "333oh", "clock", "minx",
    "pyram", "skewb", "sq1", "444bf", "555bf", "333mbf"
]

def export_split_rankings_by_type(all_people, output_single="lsc_rankings_single.csv", output_avg="lsc_rankings_average.csv"):
    print("💾 Writing split LSC rankings to CSVs...")

    # Prepare headers
    single_headers = ["name"] + [f"{e}_single" for e in EVENT_ORDER]
    avg_headers = ["name"] + [f"{e}_average" for e in EVENT_ORDER]

    with open(output_single, "w", newline="", encoding="utf-8") as f_single, \
         open(output_avg, "w", newline="", encoding="utf-8") as f_avg:

        writer_single = csv.writer(f_single)
        writer_avg = csv.writer(f_avg)

        writer_single.writerow(single_headers)
        writer_avg.writerow(avg_headers)

        for slug, ranks in sorted(all_people.items()):
            row_single = [slug]
            row_avg = [slug]
            for e in EVENT_ORDER:
                row_single.append(ranks.get(f"{e}_single", {}).get("rank", ""))
                row_avg.append(ranks.get(f"{e}_average", {}).get("rank", ""))
            writer_single.writerow(row_single)
            writer_avg.writerow(row_avg)

    print(f"✅ Exported to {output_single} and {output_avg}")

def main():
    rankings_by_name, max_ranks = fetch_rankings()
    all_people = build_full_rankings(rankings_by_name, max_ranks)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_people, f, indent=2, ensure_ascii=False)
    print(f"Saved full LSC rankings to {OUTPUT_JSON}")

    sum_single, sum_avg = compute_sum_of_ranks(all_people)
    export_sum_of_ranks_to_csv(sum_single, sum_avg)
    export_split_rankings_by_type(all_people)
    print_rankings("Single", sum_single)
    print_rankings("Average", sum_avg)

if __name__ == "__main__":
    main()
