#!/usr/bin/env python3
"""
generate_rankings.py

Reads profiles/lsc/*-merged.json, computes:
 - rankings_by_results (individual attempts and averages)
 - rankings_by_person (best per person per event/type)
 - records (top entries per event/type)

Writes results into Supabase tables:
 - public.rankings_by_results
 - public.rankings_by_person
 - public.records

Tables must match the schema you provided (attempt1..attempt5 columns, attempts jsonb, etc).
"""

import os
import json
import re
import time
from collections import defaultdict
from supabase import create_client

# Config
MERGED_DIR = os.environ.get("MERGED_DIR", "profiles/lsc")
WRITE_LOCAL_JSON = False  # set True to also write local JSON outputs
CHUNK_SIZE = 200

SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io'

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

INVALID_VALUES = {-1, -2, 0}
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "data")
if WRITE_LOCAL_JSON:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def unslug_name_from_filename(fn):
    base = os.path.splitext(fn)[0]
    if base.lower().endswith("-merged"):
        base = base[: -len("-merged")]
    s = re.sub(r'[^0-9a-zA-Z]+', ' ', base).strip()
    if any(c.isupper() for c in base):
        return s.strip()
    return s.title()

def load_merged_profiles(path):
    profiles = []
    if not os.path.isdir(path):
        raise FileNotFoundError(f"merged profiles directory not found: {path}")
    for fn in os.listdir(path):
        if not fn.lower().endswith("-merged.json"):
            continue
        fp = os.path.join(path, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            continue
        display_name = unslug_name_from_filename(fn)
        wcaid = obj.get("wcaid")
        results = obj.get("results") or []
        profiles.append({"file": fn, "name": display_name, "wcaid": wcaid, "results": results})
    return profiles

def normalize_attempts(attempts):
    if not isinstance(attempts, list):
        return None
    out = []
    for a in attempts:
        try:
            out.append(int(a))
        except Exception:
            try:
                if isinstance(a, str) and a.strip().upper() == "DNF":
                    out.append(-1)
                elif isinstance(a, str) and a.strip().upper() == "DNS":
                    out.append(-2)
                else:
                    out.append(0)
            except:
                out.append(0)
    return out

def sort_entries(entries):
    return sorted(entries, key=lambda x: (x["value"], x.get("person_name", "") or ""))

# conservative average computation (centiseconds)
def compute_average_from_attempts(attempts):
    if not attempts or not isinstance(attempts, list):
        return None
    arr = [a for a in attempts]
    if any(a in (-1, -2) for a in arr):
        return None
    positives = [a for a in arr if a and a > 0]
    if len(arr) >= 5 and len(positives) >= 5:
        sorted_vals = sorted(arr)
        middle = sorted_vals[1:4]
        return int(round(sum(middle) / 3.0))
    if len(arr) >= 3 and len(positives) >= 3:
        return int(round(sum(positives[:3]) / 3.0))
    if positives:
        return int(round(sum(positives) / len(positives)))
    return None

# --- core conversion logic (keeps original behavior) ---

def collect_event_entries(profiles):
    by_event = defaultdict(list)

    for p in profiles:
        name = p["name"]
        wcaid = p["wcaid"]

        for r in p["results"]:
            if not isinstance(r, dict):
                continue
            ev = r.get("event_id")
            if not ev:
                continue

            # numeric best/average if present
            best_val = r.get("best")
            avg_val = r.get("average")
            try:
                best_val = int(best_val) if best_val is not None else None
            except Exception:
                best_val = None
            try:
                avg_val = int(avg_val) if avg_val is not None else None
            except Exception:
                avg_val = None

            raw_attempts = r.get("attempts")
            attempts = normalize_attempts(raw_attempts) if raw_attempts is not None else None

            best_index = r.get("best_index")
            worst_index = r.get("worst_index")

            regional_single_tag = r.get("regional_single_record") if r.get("regional_single_record") is not None else None
            regional_average_tag = r.get("regional_average_record") if r.get("regional_average_record") is not None else None

            common = {
                "person_name": name,
                "wcaid": wcaid,
                "competition_id": r.get("competition_id"),
            }

            emitted_single_entries = []
            if isinstance(attempts, list) and len(attempts) > 0:
                for idx, a in enumerate(attempts):
                    try:
                        aval = int(a)
                    except Exception:
                        continue
                    if aval in INVALID_VALUES:
                        continue
                    e = dict(common)
                    e.update({
                        "value": aval,
                        "type": "single",
                        "attempt_index": idx,
                        "attempts": attempts,
                        "regional_single_record": None,
                        "regional_average_record": None,
                        "best_index": best_index if best_index is not None else None,
                        "worst_index": worst_index if worst_index is not None else None
                    })
                    emitted_single_entries.append(e)
                    by_event[ev].append(e)
            else:
                if best_val is not None and best_val not in INVALID_VALUES:
                    e = dict(common)
                    e.update({
                        "value": best_val,
                        "type": "single",
                        "attempts": None,
                        "attempt_index": None,
                        "regional_single_record": None,
                        "regional_average_record": None,
                        "best_index": best_index if best_index is not None else None,
                        "worst_index": worst_index if worst_index is not None else None
                    })
                    emitted_single_entries.append(e)
                    by_event[ev].append(e)

            # Average entry
            if avg_val is not None and avg_val not in INVALID_VALUES:
                if attempts is not None and all((int(a or 0) in INVALID_VALUES) for a in attempts):
                    pass
                else:
                    avg_entry = dict(common)
                    avg_entry.update({
                        "value": avg_val,
                        "type": "average",
                        "attempts": attempts,
                        "regional_single_record": None,
                        "regional_average_record": regional_average_tag,
                        "best_index": best_index if best_index is not None else None,
                        "worst_index": worst_index if worst_index is not None else None
                    })
                    by_event[ev].append(avg_entry)

            # Attach regional_single_record to best single
            if regional_single_tag and emitted_single_entries:
                best_single = None
                best_val_seen = None
                for s in emitted_single_entries:
                    v = s.get("value")
                    if v is None:
                        continue
                    if v < 0:
                        continue
                    if best_single is None:
                        best_single = s
                        best_val_seen = v
                    else:
                        if v < best_val_seen:
                            best_single = s
                            best_val_seen = v
                if best_single is None and best_val is not None and best_val not in INVALID_VALUES:
                    for s in emitted_single_entries:
                        if s.get("value") == best_val:
                            best_single = s
                            break
                if best_single is None and emitted_single_entries:
                    best_single = emitted_single_entries[0]
                if best_single is not None:
                    best_single["regional_single_record"] = regional_single_tag

    return by_event

def build_rankings_by_results(by_event):
    out = {}
    for ev, entries in by_event.items():
        singles = [e for e in entries if e["type"] == "single"]
        avgs = [e for e in entries if e["type"] == "average"]
        singles_sorted = sort_entries(singles)
        avgs_sorted = sort_entries(avgs)
        out[ev] = {"single": singles_sorted, "average": avgs_sorted}
    return out

def better_for_event(ev, candidate_value, current_value):
    return candidate_value < current_value

def build_rankings_by_person(rankings_by_results):
    by_person = {}
    for ev, data in rankings_by_results.items():
        per_person_single = {}
        per_person_avg = {}
        for e in data["single"]:
            key = (e.get("wcaid") or "").strip().lower() or (e.get("person_name") or "").strip().lower()
            val = e["value"]
            cur = per_person_single.get(key)
            if cur is None or better_for_event(ev, val, cur["value"]):
                per_person_single[key] = e
        for e in data["average"]:
            key = (e.get("wcaid") or "").strip().lower() or (e.get("person_name") or "").strip().lower()
            val = e["value"]
            cur = per_person_avg.get(key)
            if cur is None or better_for_event(ev, val, cur["value"]):
                per_person_avg[key] = e
        single_list = list(per_person_single.values())
        avg_list = list(per_person_avg.values())
        single_sorted = sort_entries(single_list)
        avg_sorted = sort_entries(avg_list)
        by_person[ev] = {"single": single_sorted, "average": avg_sorted}
    return by_person

def build_records(rankings_by_results):
    recs = {}
    for ev, data in rankings_by_results.items():
        recs[ev] = {"single": [], "average": []}
        for cat in ("single", "average"):
            entries = data[cat]
            if not entries:
                continue
            best_val = min(e["value"] for e in entries)
            tops = [e for e in entries if e["value"] == best_val]
            recs[ev][cat] = tops
    return recs

# --- Supabase helpers ---

def _resp_data_or_raise(resp, context_msg="Supabase request"):
    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp.get("data")
    err = getattr(resp, "error", None) or (resp.get("error") if isinstance(resp, dict) else None)
    if err:
        raise RuntimeError(f"{context_msg}: {err}")
    return data or []

def _delete_all_from_table(table_name):
    try:
        resp = supabase.table(table_name).delete().neq("id", 0).execute()
        _resp_data_or_raise(resp, f"delete all from {table_name}")
    except Exception as e:
        raise RuntimeError(f"Failed to clear table {table_name}: {e}")

def _insert_rows_chunked(table_name, rows):
    inserted = []
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i:i+CHUNK_SIZE]
        try:
            resp = supabase.table(table_name).insert(chunk).execute()
            data = _resp_data_or_raise(resp, f"insert into {table_name}")
            if isinstance(data, list):
                inserted.extend(data)
            elif isinstance(data, dict):
                inserted.append(data)
        except Exception as e:
            raise RuntimeError(f"Failed inserting chunk into {table_name}: {e}")
        time.sleep(0.05)
    return inserted

def main():
    profiles = load_merged_profiles(MERGED_DIR)
    by_event = collect_event_entries(profiles)
    rankings_by_results = build_rankings_by_results(by_event)
    rankings_by_person = build_rankings_by_person(rankings_by_results)
    records = build_records(rankings_by_results)

    results_rows = []
    for ev, data in rankings_by_results.items():
        for cat in ("single", "average"):
            prev_value = None
            prev_rank = 0
            for idx, e in enumerate(data[cat]):
                value = int(e.get("value")) if e.get("value") is not None else None
                rank = prev_rank if value == prev_value else idx + 1
                prev_value = value
                prev_rank = rank

                attempts = e.get("attempts") if isinstance(e.get("attempts"), list) else None
                a1 = attempts[0] if attempts and len(attempts) > 0 else None
                a2 = attempts[1] if attempts and len(attempts) > 1 else None
                a3 = attempts[2] if attempts and len(attempts) > 2 else None
                a4 = attempts[3] if attempts and len(attempts) > 3 else None
                a5 = attempts[4] if attempts and len(attempts) > 4 else None

                row = {
                    "rank": rank,
                    "event_id": ev,
                    "type": cat,
                    "person_name": e.get("person_name"),
                    "wcaid": e.get("wcaid"),
                    "competition_id": e.get("competition_id"),
                    "value": value,
                    "attempt_index": e.get("attempt_index"),
                    "attempt1": a1,
                    "attempt2": a2,
                    "attempt3": a3,
                    "attempt4": a4,
                    "attempt5": a5,
                    "regional_single_record": e.get("regional_single_record"),
                    "regional_average_record": e.get("regional_average_record"),
                    "best_index": e.get("best_index"),
                    "worst_index": e.get("worst_index"),
                    "notes": None
                }
                results_rows.append(row)

    person_rows = []
    for ev, data in rankings_by_person.items():
        for cat in ("single", "average"):
            prev_value = None
            prev_rank = 0
            for idx, e in enumerate(data[cat]):
                value = int(e.get("value")) if e.get("value") is not None else None
                rank = prev_rank if value == prev_value else idx + 1
                prev_value = value
                prev_rank = rank

                attempts = e.get("attempts") if isinstance(e.get("attempts"), list) else None
                a1 = attempts[0] if attempts and len(attempts) > 0 else None
                a2 = attempts[1] if attempts and len(attempts) > 1 else None
                a3 = attempts[2] if attempts and len(attempts) > 2 else None
                a4 = attempts[3] if attempts and len(attempts) > 3 else None
                a5 = attempts[4] if attempts and len(attempts) > 4 else None

                row = {
                    "rank": rank,
                    "event_id": ev,
                    "type": cat,
                    "person_name": e.get("person_name"),
                    "wcaid": e.get("wcaid"),
                    "competition_id": e.get("competition_id"),
                    "value": value,
                    "attempt1": a1,
                    "attempt2": a2,
                    "attempt3": a3,
                    "attempt4": a4,
                    "attempt5": a5,
                }
                person_rows.append(row)

    records_rows = []
    for ev, cats in records.items():
        for cat in ("single", "average"):
            entries = cats.get(cat) or []
            for e in entries:
                attempts = e.get("attempts") if isinstance(e.get("attempts"), list) else None
                a1 = attempts[0] if attempts and len(attempts) > 0 else None
                a2 = attempts[1] if attempts and len(attempts) > 1 else None
                a3 = attempts[2] if attempts and len(attempts) > 2 else None
                a4 = attempts[3] if attempts and len(attempts) > 3 else None
                a5 = attempts[4] if attempts and len(attempts) > 4 else None

                row = {
                    "event_id": ev,
                    "type": cat,
                    "person_name": e.get("person_name"),
                    "wcaid": e.get("wcaid"),
                    "competition_id": e.get("competition_id"),
                    "value": int(e.get("value")) if e.get("value") is not None else None,
                    "attempt_index": e.get("attempt_index"),
                    "attempt1": a1,
                    "attempt2": a2,
                    "attempt3": a3,
                    "attempt4": a4,
                    "attempt5": a5,
                    "regional_single_record": e.get("regional_single_record"),
                    "regional_average_record": e.get("regional_average_record"),
                    "best_index": e.get("best_index"),
                    "worst_index": e.get("worst_index"),
                }
                records_rows.append(row)

    print("Clearing existing tables...")
    _delete_all_from_table("rankings_by_results")
    _delete_all_from_table("rankings_by_person")
    _delete_all_from_table("records")

    print(f"Inserting {len(results_rows)} rows into rankings_by_results...")
    _insert_rows_chunked("rankings_by_results", results_rows)
    print(f"Inserting {len(person_rows)} rows into rankings_by_person...")
    _insert_rows_chunked("rankings_by_person", person_rows)
    print(f"Inserting {len(records_rows)} rows into records...")
    _insert_rows_chunked("records", records_rows)

    print("Done. Rankings written to Supabase tables.")

if __name__ == "__main__":
    main()
