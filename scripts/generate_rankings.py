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

def remove_old_outputs(filenames):
    # Remove previous output files if present so we always produce fresh files
    for fn in filenames:
        path = os.path.join(OUTPUT_DIR, fn)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            # non-fatal if cleanup fails
            pass


def collect_event_entries(profiles):
    """
    Build per-event entries.

    Behavior:
    - Export individual attempt singles (each valid attempt -> single entry).
    - Attach regional_average_record to the average entry when present.
    - Attach regional_single_record to exactly one single entry: the best single for that result
      defined as the smallest non-negative numeric single (prefer attempts by value; if attempts absent
      use result.best). If multiple identical best values exist, attach to the first emitted single
      with that value.
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

            # normalized attempts (list or None)
            raw_attempts = r.get("attempts")
            attempts = normalize_attempts(raw_attempts) if raw_attempts is not None else None

            best_index = r.get("best_index")
            worst_index = r.get("worst_index")

            # record tags from source result (may be None or a string like "NR", "AsR", "WR")
            regional_single_tag = r.get("regional_single_record") if r.get("regional_single_record") is not None else None
            regional_average_tag = r.get("regional_average_record") if r.get("regional_average_record") is not None else None

            common = {
                "person_name": name,
                "wcaid": wcaid,
                "competition_id": r.get("competition_id"),
                "round_name": r.get("round_name") or None
            }

            # Emit single entries for attempts (track emitted singles so we can tag the best one later)
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
                        # regional tags: default None; will attach to best single later if applicable
                        "regional_single_record": None,
                        "regional_average_record": None,
                        "best_index": best_index if best_index is not None else None,
                        "worst_index": worst_index if worst_index is not None else None
                    })
                    emitted_single_entries.append(e)
                    by_event[ev].append(e)
            else:
                # fallback: no attempts list — use best as a single entry if valid
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

            # Average entry (if valid) — attach regional_average_record to the average entry
            if avg_val is not None and avg_val not in INVALID_VALUES:
                if attempts is not None and all((int(a or 0) in INVALID_VALUES) for a in attempts):
                    # skip average derived from entirely-invalid attempts
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

            # Attach regional_single_record to the best single: defined as smallest non-negative numeric value
            if regional_single_tag and emitted_single_entries:
                # find numeric best among emitted singles (smallest non-negative)
                best_single = None
                best_val_seen = None
                for s in emitted_single_entries:
                    v = s.get("value")
                    if v is None:
                        continue
                    # skip negative invalid markers (they should have been filtered) but be defensive
                    if v < 0:
                        continue
                    if best_single is None:
                        best_single = s
                        best_val_seen = v
                    else:
                        # choose smaller numeric value
                        if v < best_val_seen:
                            best_single = s
                            best_val_seen = v
                # If no non-negative single found above but best_val exists and is valid, try to match by best_val
                if best_single is None and best_val is not None and best_val not in INVALID_VALUES:
                    for s in emitted_single_entries:
                        if s.get("value") == best_val:
                            best_single = s
                            break
                # As a safe fallback, if still none, pick the first emitted single
                if best_single is None and emitted_single_entries:
                    best_single = emitted_single_entries[0]
                # attach tag if we found one
                if best_single is not None:
                    best_single["regional_single_record"] = regional_single_tag

    return by_event


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

def sort_entries(entries):
    return sorted(entries, key=lambda x: (x["value"], x.get("person_name", "")))

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

def save_json(obj, filename):
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def main():
    remove_old_outputs([
        "rankings_by_results.json",
        "rankings_by_person.json",
        "records.json"
    ])

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
