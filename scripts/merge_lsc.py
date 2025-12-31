#!/usr/bin/env python3
import os
import json
import re
import tempfile
from typing import List, Dict, Optional
from supabase import create_client, Client

# --- Configuration ---
SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io'

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

WCA_CACHE_DIR = "profiles/wca"
LSC_CACHE_DIR = "profiles/lsc"

FORMAT_MAP = {
    'ao5': 'a', 'mo3': 'm', 'bo3': '3', 'bo2': '2', 'bo1': '1'
}

# --- Utilities ---
def ensure_dir(path): os.makedirs(path, exist_ok=True)

def name_to_slug(name): return re.sub(r'[^\w]+', '-', name.strip().lower()).strip('-') or "unknown"

def atomic_write(path, obj):
    ensure_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix="tmp", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def parse_attempt(v):
    if v is None:
        return 0

    # Handle strings
    if isinstance(v, str):
        s = v.strip()
        up = s.upper()
        if up == "DNF":
            return -1
        if up == "DNS":
            return -2
        try:
            # Try numeric parse from string
            num = float(s)
        except:
            return 0
        v = num

    # Handle numeric types
    try:
        # Floats that are not integers are assumed to be seconds -> convert to centiseconds
        if isinstance(v, float) and not v.is_integer():
            return int(round(v * 100))

        # Integers (or floats that are effectively integers)
        iv = int(round(v))

        # Heuristic: if it's extremely large, it's likely milliseconds; convert to centiseconds
        # e.g., 123456 ms -> 12345 cs
        if iv >= 100000:
            return iv // 10

        # Otherwise, treat as already-centiseconds and keep as-is
        return iv
    except:
        return 0

def compute_best_and_indices(attempts):
    positives = [a for a in attempts if a > 0]
    best = min(positives) if positives else None
    best_index = next((i for i, a in enumerate(attempts) if a == best), None)
    if -1 in attempts: worst_index = attempts.index(-1)
    elif -2 in attempts: worst_index = attempts.index(-2)
    elif positives:
        worst = max(positives)
        worst_index = next((i for i, a in enumerate(attempts) if a == worst), None)
    else:
        worst_index = None
    return best, best_index, worst_index

def compute_average_from_attempts(attempts: List[Optional[int]], isFMC: bool, format_id: Optional[str]) -> Optional[int]:
    if not attempts:
        return None
    arr = attempts[:5]
    if format_id == 'a':
        if any(a is None for a in arr): return None
        penalties = sum(1 for a in arr if a in (-1, -2))
        if penalties > 1: return -1
        positives = [a for a in arr if isinstance(a, (int, float)) and a > 0]
        if not positives: return -1
        best_to_drop = min(positives)
        worst_to_drop = -2 if -2 in arr else -1 if -1 in arr else max(positives)
        remaining = arr[:]
        try: remaining.remove(best_to_drop)
        except: pass
        try: remaining.remove(worst_to_drop)
        except: pass
        middle = [v for v in remaining if isinstance(v, (int, float)) and v > 0]
        return round(sum(middle) / 3) if len(middle) == 3 else -1
    elif format_id in ('m', '3'):
        valid = [v for v in arr[:3] if v not in (None, 0, -1, -2)]
        if sum(1 for v in arr[:3] if v in (-1, -2)) > 0 or len(valid) < 3: return -1
        avg = sum(valid) / 3
        return round(avg * 100) if isFMC else round(avg)
    elif format_id == '2':
        two = arr[:2]
        if any(a in (-1, -2) for a in two): return None
        positives = [a for a in two if a and a > 0]
        return min(positives) if positives else None
    elif format_id == '1':
        a = arr[0]
        return a if a and a > 0 else None
    positives = [a for a in arr if isinstance(a, (int, float)) and a > 0]
    return round(sum(positives) / len(positives)) if len(positives) >= 3 else None

def load_json_if_exists(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return None
    return None

def replace_competition_ids_with_names_from_supabase():
    print("Fetching WCA competition titles from Supabase...")
    try:
        resp = supabase.table("wca_comp_titles").select("comp_id, comp_name").execute()
        rows = resp.data or []
        comp_name_map = {r["comp_id"]: r["comp_name"] for r in rows if r.get("comp_id") and r.get("comp_name")}
    except Exception as e:
        print(f"⚠️ Failed to fetch competition titles: {e}")
        return

    print("Replacing competition_id with comp_name in merged profiles...")
    for fn in os.listdir(LSC_CACHE_DIR):
        if not fn.endswith("-merged.json"):
            continue
        path = os.path.join(LSC_CACHE_DIR, fn)
        data = load_json_if_exists(path)
        if not data or "results" not in data:
            continue

        modified = False
        for r in data["results"]:
            cid = r.get("competition_id")
            if cid in comp_name_map:
                r["competition_id"] = comp_name_map[cid]
                modified = True

        if modified:
            atomic_write(path, data)
            print(f"✔ Updated: {fn}")

def copy_wca_profiles_without_lsc_results():
    print("Copying WCA profiles without LSC results...")
    ensure_dir(WCA_CACHE_DIR)
    ensure_dir(LSC_CACHE_DIR)

    for fn in os.listdir(WCA_CACHE_DIR):
        if not fn.endswith(".json"):
            continue
        slug = os.path.splitext(fn)[0]
        merged_fn = f"{slug}-merged.json"
        merged_path = os.path.join(LSC_CACHE_DIR, merged_fn)
        if os.path.exists(merged_path):
            continue  # already created during LSC merge

        wca_path = os.path.join(WCA_CACHE_DIR, fn)
        data = load_json_if_exists(wca_path)
        if not data:
            continue

        print(f"→ Copying WCA profile: {slug}")
        atomic_write(merged_path, data)

def main():
    ensure_dir(WCA_CACHE_DIR)
    ensure_dir(LSC_CACHE_DIR)

    print("Fetching all round_results...")
    rr_resp = supabase.table("round_results").select("*").execute()
    round_results = rr_resp.data or []

    print("Fetching all rounds...")
    round_ids = list({r["round_id"] for r in round_results})
    rounds_resp = supabase.table("rounds").select("*").in_("id", round_ids).execute()
    round_meta = {r["id"]: r for r in rounds_resp.data or []}

    print("Fetching competitions...")
    comp_ids = list({r["comp_id"] for r in round_meta.values()})
    comps_resp = supabase.table("competitions").select("id,name").in_("id", comp_ids).execute()
    comp_meta = {c["id"]: c["name"] for c in comps_resp.data or []}

    print("Grouping results by competitor...")
    results_by_name = {}

    for row in round_results:
        name = (row.get("competitor_name") or "").strip()
        if not name:
            continue

        round_info = round_meta.get(row["round_id"], {})
        comp_name = comp_meta.get(round_info.get("comp_id"), round_info.get("comp_id"))
        event_id = round_info.get("event_id")
        round_code = round_info.get("round_code")
        fmt = round_info.get("format")
        round_date = round_info.get("date")

        attempts = [parse_attempt(row.get(f"attempt{i}")) for i in range(1, 6)]
        attempts = (attempts + [0] * 5)[:5]

        best, best_index, worst_index = compute_best_and_indices(attempts)
        avg_val = parse_attempt(row.get("average")) if row.get("average") is not None else None
        if avg_val is None:
            avg_val = compute_average_from_attempts(attempts, event_id == "333fm", FORMAT_MAP.get(fmt, fmt))

        result = {
            "pos": int(row.get("pos")) if row.get("pos") not in (None, "") else None,
            "competition_id": comp_name,
            "event_id": event_id,
            "round_type_id": round_code,
            "format_id": FORMAT_MAP.get(fmt, fmt),
            "attempts": attempts,
            "best": parse_attempt(row.get("best")) if row.get("best") is not None else best,
            "average": avg_val,
            "best_index": best_index,
            "worst_index": worst_index,
            "round_date": round_date,
        }

        results_by_name.setdefault(name, []).append(result)

    print("Writing merged profiles...")
    for name, results in results_by_name.items():
        print(f"→ {name}")
        slug = name_to_slug(name)
        merged_path = os.path.join(LSC_CACHE_DIR, f"{slug}-merged.json")
        existing = load_json_if_exists(os.path.join(WCA_CACHE_DIR, f"{slug}.json")) or {}
        merged = existing.copy()
        merged["person"] = {"name": name}
        merged["results"] = merged.get("results", [])
        for r in results:
            merged["results"].append(r)
        atomic_write(merged_path, merged)

    print(f"Wrote {len(results_by_name)} merged profiles to {LSC_CACHE_DIR}")

    copy_wca_profiles_without_lsc_results()
    print("WCA profiles without LSC results copied.")

    replace_competition_ids_with_names_from_supabase()
    print("WCA Competition IDs replaced with competition names.")

    print("Done.")

if __name__ == "__main__":
    main()