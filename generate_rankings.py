#generate_rankings.py
"""
Produces:
 - data/rankings_by_results.json
 - data/rankings_by_person.json
 - data/records.json

Input: profiles/lsc/{nameslug}-merged.json

Changes:
 - Every valid attempt in a result's attempts array is exported as its own "single" entry (with attempt_index).
 - If attempts are missing, fall back to exporting the result.best as a single entry when valid.
 - Averages are exported as before; an average is not required for single entries to be exported.
"""
import os
import json
import re
from collections import defaultdict

MERGED_DIR = os.environ.get("MERGED_DIR", "profiles/lsc")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

INVALID_VALUES = {-1, -2, 0}

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

def attempts_all_invalid(attempts):
    if not isinstance(attempts, list):
        return True
    for a in attempts:
        try:
            if int(a or 0) != 0:
                return False
        except Exception:
            return False
    return True

def normalize_attempts(attempts):
    if not isinstance(attempts, list):
        return None
    out = []
    for a in attempts:
        try:
            out.append(int(a))
        except Exception:
            # preserve special tokens if already -1/-2, else treat as 0
            try:
                if isinstance(a, str) and a.strip().upper() == "DNF":
                    out.append(-1)
                elif isinstance(a, str) and a.strip().upper() == "DNS":
                    out.append(-2)
                else:
                    out.append(0)
            except:
                out.append(0)
    # keep as-is length (do not force pad here) — rendering adapts to round length
    return out

def collect_event_entries(profiles):
    """
    Build per-event entries.

    Rules:
     - If a result contains an attempts array, export each non-invalid attempt as a separate "single" entry.
       Each such single includes:
         - value (int)
         - type: "single"
         - attempt_index (0-based)
         - attempts (the full attempts array normalized)
         - best_index / worst_index copied from the result if present
     - If attempts array is absent or not a list, export a single "single" entry using result.best when valid.
     - Averages are exported as before, skipped only when attempts exist and are all invalid.
     - Singles are exported regardless of whether an average exists for that result.
    """
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

            # parse numeric best/average (int when possible)
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

            # normalize attempts if present
            raw_attempts = r.get("attempts")
            attempts = normalize_attempts(raw_attempts) if raw_attempts is not None else None

            # indices from source result (may be None)
            best_index = r.get("best_index")
            worst_index = r.get("worst_index")

            common = {
                "person_name": name,
                "wcaid": wcaid,
                "competition_id": r.get("competition_id"),
                "round_name": r.get("round_name") or None
            }

            # SINGLE handling:
            # If attempts array exists and is a list, export each attempt as its own single entry (except invalid tokens).
            if isinstance(attempts, list) and len(attempts) > 0:
                for idx, a in enumerate(attempts):
                    try:
                        aval = int(a)
                    except Exception:
                        continue
                    if aval in INVALID_VALUES:
                        # skip invalid attempts for single entries
                        continue
                    e = dict(common)
                    e.update({
                        "value": aval,
                        "type": "single",
                        "attempt_index": idx,
                        "attempts": attempts,
                        "best_index": best_index if best_index is not None else None,
                        "worst_index": worst_index if worst_index is not None else None
                    })
                    by_event[ev].append(e)
            else:
                # fallback: if no attempts array present, use best_val as single (if valid)
                if best_val is not None and best_val not in INVALID_VALUES:
                    e = dict(common)
                    e.update({
                        "value": best_val,
                        "type": "single",
                        "attempts": attempts,  # None
                        "best_index": best_index if best_index is not None else None,
                        "worst_index": worst_index if worst_index is not None else None
                    })
                    by_event[ev].append(e)

            # AVERAGE handling (unchanged except skip when attempts exist and are all invalid)
            if avg_val is not None and avg_val not in INVALID_VALUES:
                if attempts is not None and all((int(a or 0) in INVALID_VALUES) for a in attempts):
                    # skip average derived from entirely-invalid attempts
                    pass
                else:
                    e = dict(common)
                    e.update({
                        "value": avg_val,
                        "type": "average",
                        "attempts": attempts,
                        "best_index": best_index if best_index is not None else None,
                        "worst_index": worst_index if worst_index is not None else None
                    })
                    by_event[ev].append(e)
    return by_event

def sort_entries(entries, event_id):
    # For 333mbf higher is better
    reverse = (event_id == "333mbf")
    # sort by numeric value (descending for 333mbf), then by person_name for determinism
    return sorted(entries, key=lambda x: ((-x["value"]) if reverse else x["value"], x.get("person_name") or ""))

def build_rankings_by_results(by_event):
    out = {}
    for ev, entries in by_event.items():
        singles = [e for e in entries if e["type"] == "single"]
        avgs = [e for e in entries if e["type"] == "average"]
        singles_sorted = sort_entries(singles, ev)
        avgs_sorted = sort_entries(avgs, ev)
        out[ev] = {"single": singles_sorted, "average": avgs_sorted}
    return out

def better_for_event(ev, candidate_value, current_value):
    if ev == "333mbf":
        return candidate_value > current_value
    return candidate_value < current_value

def build_rankings_by_person(rankings_by_results):
    by_person = {}
    for ev, data in rankings_by_results.items():
        per_person_single = {}
        per_person_avg = {}
        # For singles: pick the best single per person among all single entries (now includes individual attempts)
        for e in data["single"]:
            key = (e.get("wcaid") or "").strip().lower() or (e.get("person_name") or "").strip().lower()
            val = e["value"]
            cur = per_person_single.get(key)
            if cur is None or better_for_event(ev, val, cur["value"]):
                per_person_single[key] = e
        # For averages: pick best average per person
        for e in data["average"]:
            key = (e.get("wcaid") or "").strip().lower() or (e.get("person_name") or "").strip().lower()
            val = e["value"]
            cur = per_person_avg.get(key)
            if cur is None or better_for_event(ev, val, cur["value"]):
                per_person_avg[key] = e
        single_list = list(per_person_single.values())
        avg_list = list(per_person_avg.values())
        single_sorted = sort_entries(single_list, ev)
        avg_sorted = sort_entries(avg_list, ev)
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
            if ev == "333mbf":
                best_val = max(e["value"] for e in entries)
            else:
                best_val = min(e["value"] for e in entries)
            tops = [e for e in entries if e["value"] == best_val]
            recs[ev][cat] = tops
    return recs

def save_json(obj, filename):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def main():
    profiles = load_merged_profiles(MERGED_DIR)
    by_event = collect_event_entries(profiles)
    rankings_by_results = build_rankings_by_results(by_event)
    rankings_by_person = build_rankings_by_person(rankings_by_results)
    records = build_records(rankings_by_results)

    save_json(rankings_by_results, "rankings_by_results.json")
    save_json(rankings_by_person, "rankings_by_person.json")
    save_json(records, "records.json")
    return rankings_by_results, rankings_by_person, records

if __name__ == "__main__":
    main()
