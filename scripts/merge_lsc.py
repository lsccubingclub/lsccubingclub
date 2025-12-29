#!/usr/bin/env python3
"""
merge_lsc_from_supabase.py

Rewritten merge_lsc.py to fetch competition metadata, rounds and results from Supabase
instead of Google Sheets.

Behavior:
- Reads locked competitions from the `competitions` table (rows where locked is true).
- For each competition, reads rounds from `rounds` table and results from `round_results`.
- Matches competitors to WCA profiles (from a `wca_ids` table if present, else from local WCA cache).
- Produces merged profile files under profiles/lsc/{nameslug}-merged.json, merging with any
  existing profiles/wca/{nameslug}.json as before.

Assumptions (adjust if your schema differs):
- competitions table: id, name (or title), locked (boolean)
- rounds table: id, comp_id, event_id (human name or code), round_code (e.g. '1','f'), format, advance_to_next, date
- round_results table: id, round_id, competitor_name, attempt1..attempt5, pos, best, average, best_index, worst_index
- Optional wca_ids table: wcaid, name

Environment:
- SUPABASE_URL
- SUPABASE_KEY (service role or anon key with read permissions)

Run:
    SUPABASE_URL="https://xyz.supabase.co" SUPABASE_KEY="..." python merge_lsc_from_supabase.py
"""

import os
import json
import re
import tempfile
import time
import logging
from typing import Dict, Optional, List

from supabase import create_client, Client

# Local config (same as original)
import config  # optional; original script referenced config for cache dirs and credentials

WCA_CACHE_DIR = getattr(config, "WCA_CACHE_DIR", "profiles/wca")
LSC_CACHE_DIR = getattr(config, "LSC_CACHE_DIR", "profiles/lsc")
WCA_TITLE_CACHE_PATH = os.environ.get("WCA_TITLE_CACHE", "wca_comp_titles.json")

# Supabase client
SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io'

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Constants and mappings (kept from original)
EVENT_NAME_TO_CODE = {
    '3x3x3 Cube': '333', '2x2x2 Cube': '222', '4x4x4 Cube': '444', '5x5x5 Cube': '555',
    '6x6x6 Cube': '666', '7x7x7 Cube': '777', '3x3x3 Blindfolded': '333bf',
    '3x3x3 Fewest Moves': '333fm', '3x3x3 One-Handed': '333oh', 'Clock': 'clock',
    'Megaminx': 'minx', 'Pyraminx': 'pyram', 'Skewb': 'skewb', 'Square-1': 'sq1',
    '4x4x4 Blindfolded': '444bf', '5x5x5 Blindfolded': '555bf', '3x3x3 Multi-Blind': '333mbf'
}

ROUND_TYPE_MAP = {
    'First Round': '1', 'Second Round': '2', 'Third Round': '3', 'Final': 'f'
}

FORMAT_MAP = {
    'ao5': 'a', 'mo3': 'm', 'bo3': '3', 'bo2': '2', 'bo1': '1'
}

# Helpers (adapted from original)
def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def atomic_write(path, obj):
    dirn = os.path.dirname(path)
    ensure_dir(dirn)
    fd, tmp = tempfile.mkstemp(prefix="tmp", dir=dirn)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

def name_to_slug(name):
    s = (name or "").strip().lower()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'-{2,}', '-', s)
    s = s.strip('-')
    return s or "unknown"

def load_json_if_exists(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def parse_attempt_value_from_db(v, event_id):
    """
    Input v is expected to be numeric (milliseconds) or special codes (-1 DNF, -2 DNS).
    Keep behavior consistent with original parse_attempt_value which returned centiseconds.
    If DB stores milliseconds, convert to centiseconds (divide by 10).
    If DB already stores centiseconds, keep as-is.
    We'll attempt to be permissive:
      - None -> 0
      - int/float -> round to int
      - strings -> try parse
    """
    if v is None:
        return 0
    try:
        if isinstance(v, str):
            s = v.strip()
            if s == "":
                return 0
            if s.upper() == "DNF":
                return -1
            if s.upper() == "DNS":
                return -2
            # try float
            n = float(s)
        else:
            n = float(v)
    except Exception:
        return 0

    # If value looks like milliseconds (large), convert to centiseconds
    # Heuristic: if n > 100000 (1000s in ms) treat as ms; if n > 10000 treat as ms too.
    if n > 100000 or (n > 10000 and n % 1 == 0):
        # assume milliseconds -> convert to centiseconds
        return int(round(n / 10.0))
    # if n looks like seconds (e.g., 12.34) convert to centiseconds
    if n < 1000 and n != int(n):
        return int(round(n * 100))
    # otherwise assume already centiseconds or integer centiseconds
    return int(round(n))

def compute_best_and_indices(attempts_list, event_id):
    positives = [a for a in attempts_list if a > 0]
    best = min(positives) if positives else None
    best_index = None
    worst_index = None
    if best is not None:
        for i, a in enumerate(attempts_list):
            if a == best:
                best_index = i
                break
    if any(a == -1 for a in attempts_list):
        worst_index = attempts_list.index(-1)
    elif any(a == -2 for a in attempts_list):
        worst_index = attempts_list.index(-2)
    elif positives:
        worst_val = max(positives)
        for i, a in enumerate(attempts_list):
            if a == worst_val:
                worst_index = i
                break
    return best, best_index, worst_index

def result_fingerprint(r):
    return (
        str(r.get("competition_id") or "") + "|" +
        str(r.get("event_id") or "") + "|" +
        str(r.get("pos") or "") + "|" +
        str(r.get("round_date") or "")
    )

def wca_profile_path_for_name(name):
    return os.path.join(WCA_CACHE_DIR, f"{name_to_slug(name)}.json")

def merged_path_for_name(name):
    return os.path.join(LSC_CACHE_DIR, f"{name_to_slug(name)}-merged.json")

# Minimal helpers from original workflow
def clear_merged_files():
    ensure_dir(LSC_CACHE_DIR)
    for fn in os.listdir(LSC_CACHE_DIR):
        if fn.endswith("-merged.json"):
            path = os.path.join(LSC_CACHE_DIR, fn)
            try:
                atomic_write(path, {})  # write empty object
            except Exception:
                logging.warning("Failed to clear merged file %s", path)

def copy_wca_profiles_to_merged():
    ensure_dir(LSC_CACHE_DIR)
    if not os.path.isdir(WCA_CACHE_DIR):
        return
    for fn in os.listdir(WCA_CACHE_DIR):
        if not fn.lower().endswith(".json"):
            continue
        src = os.path.join(WCA_CACHE_DIR, fn)
        data = load_json_if_exists(src) or {}
        base = os.path.splitext(fn)[0]
        merged_fn = f"{base}-merged.json"
        dst = os.path.join(LSC_CACHE_DIR, merged_fn)
        try:
            atomic_write(dst, data)
        except Exception:
            logging.warning("Failed to copy WCA profile %s to merged %s", src, dst)

def replace_competition_ids_with_names_from_cache():
    cache = load_json_if_exists(WCA_TITLE_CACHE_PATH) or {}
    if not cache:
        return
    for fn in os.listdir(LSC_CACHE_DIR):
        if not fn.endswith("-merged.json"):
            continue
        path = os.path.join(LSC_CACHE_DIR, fn)
        data = load_json_if_exists(path)
        if not data:
            continue
        modified = False
        results = data.get("results") or []
        for r in results:
            cid = r.get("competition_id")
            if isinstance(cid, str) and cid in cache and cache[cid] != cid:
                r["competition_id"] = cache[cid]
                modified = True
        if modified:
            try:
                atomic_write(path, data)
            except Exception:
                logging.warning("Failed to update competition_id in merged file %s", path)

# -------------------------
# Average calculation helper
# -------------------------
def compute_average_from_attempts(attempts: List[int], isFMC: bool, format_id: Optional[str]) -> Optional[int]:
    if not attempts:
        return None

    # normalize length
    arr = list(attempts)[:5] + [0] * max(0, 5 - len(attempts))
    # helper checks
    has_dnf = any(a == -1 for a in arr)
    has_dns = any(a == -2 for a in arr)

    # Average of 5 (ao5)
    if format_id == 'a':
        # require five numeric attempts (0 treated as missing)
        # if any DNF/DNS present, average is undefined per this implementation
        if has_dnf or has_dns:
            return None
        positives = [a for a in arr if a and a > 0]
        if len(positives) < 3:
            return None
        # drop min and max from the five attempts (use raw arr, not positives)
        # but ensure we have five non-zero attempts; if zeros present treat as invalid
        if any(a == 0 for a in arr):
            # if zeros present, but there are at least 3 positives, still compute on positives?
            # follow conservative rule: require 5 attempts for ao5
            return None
        sorted_vals = sorted(arr)
        middle = sorted_vals[1:4]
        avg = int(round(sum(middle) / 3.0))
        return avg

    # Mean of 3 (mo3) or bo3-like formats
    if format_id == 'm' or format_id == '3' :
        # use first 3 attempts
        three = arr[:3]
        if any(a in (-1, -2) for a in three):
            return None
        positives = [a for a in three if a and a > 0]
        if not positives:
            return None
        if isFMC:
            avg = int(100* round(sum(positives) / len(positives)))
        else:
            avg = int(round(sum(positives) / len(positives)))
        return avg

    # If format_id is numeric string like '2' or '1' (best-of), return best (min positive) or single attempt
    if format_id == '2':
        # best of 2 -> min of two positive attempts if present
        two = arr[:2]
        if any(a in (-1, -2) for a in two):
            return None
        positives = [a for a in two if a and a > 0]
        if not positives:
            return None
        return min(positives)
    if format_id == '1':
        a = arr[0]
        return a if a and a > 0 else None

    # Fallback: if there are 3 non-zero positive attempts, compute mean
    positives = [a for a in arr if a and a > 0]
    if len(positives) >= 3:
        avg = int(round(sum(positives) / len(positives)))
        return avg

    return None

# -------------------------
# Supabase upsert helper for lsc_profile
# -------------------------
def _upsert_lsc_profile_to_supabase(wcaid: Optional[str], name: str, lsc_profile_obj: dict):
    """
    Upsert the lsc_profile JSON into wca_ids table.
    - If wcaid is present, find row by wcaid and update lsc_profile.
    - Otherwise try to find by exact name; if not found, insert a new row with name and lsc_profile.
    Uses find-then-insert/update to avoid ON CONFLICT dependency.
    """
    # prepare payload
    payload = {
        "name": name,
        "lsc_profile": lsc_profile_obj
    }
    if wcaid:
        payload["wcaid"] = wcaid

    # try find by wcaid first
    existing = None
    try:
        if wcaid:
            resp = supabase.table("wca_ids").select("*").eq("wcaid", wcaid).limit(1).execute()
            rows = _resp_data_or_raise(resp, "find wca row by wcaid")
            if rows:
                existing = rows[0]
        if not existing:
            # try find by exact name
            resp = supabase.table("wca_ids").select("*").eq("name", name).limit(1).execute()
            rows = _resp_data_or_raise(resp, "find wca row by name")
            if rows:
                existing = rows[0]
    except Exception as e:
        logging.warning("Supabase lookup failed for wcaid=%s name=%s: %s", wcaid, name, e)
        existing = None

    try:
        if existing and existing.get("id"):
            # update
            resp = supabase.table("wca_ids").update(payload).eq("id", existing["id"]).execute()
            _resp_data_or_raise(resp, "update wca_ids lsc_profile")
            logging.debug("Updated lsc_profile for wca_ids id=%s name=%s", existing["id"], name)
        else:
            # insert new row
            resp = supabase.table("wca_ids").insert(payload).execute()
            _resp_data_or_raise(resp, "insert wca_ids lsc_profile")
            logging.debug("Inserted new wca_ids row for name=%s", name)
    except Exception as e:
        logging.warning("Failed to upsert lsc_profile for name=%s wcaid=%s: %s", name, wcaid, e)

# -------------------------
# Replace write_merged_for_name to also update DB
# -------------------------
def write_merged_for_name(name, matched_wcaid, lsc_results):
    """
    Write merged profile to disk (same as before) and upsert lsc_profile into Supabase.
    Returns the merged object.
    """
    ensure_dir(WCA_CACHE_DIR)
    slug_profile_path = wca_profile_path_for_name(name)
    merged_path = merged_path_for_name(name)
    slug_profile = load_json_if_exists(slug_profile_path)
    existing_merged = load_json_if_exists(merged_path)

    if existing_merged is not None:
        merged = existing_merged
    elif slug_profile is not None:
        merged = slug_profile.copy()
        merged.setdefault("results", [])
    else:
        merged = {"wcaid": (matched_wcaid if matched_wcaid else None), "person": {"name": name}, "results": []}

    merged.setdefault("person", {})
    merged["person"]["name"] = name
    if merged.get("wcaid") is None and matched_wcaid:
        merged["wcaid"] = matched_wcaid

    merged.setdefault("results", [])
    existing_fps = set(result_fingerprint(r) for r in merged["results"])
    for r in lsc_results:
        # compute average if missing and format available
        fmt = r.get("format_id")
        event = r.get("event_id")
        if r.get("average") is None:
            avg = compute_average_from_attempts(r.get("attempts", []), (event == "333fm"), fmt)
            r["average"] = avg
        fp = result_fingerprint(r)
        if fp not in existing_fps:
            merged["results"].append(r)
            existing_fps.add(fp)

    if merged.get("wcaid") is None and matched_wcaid:
        merged["wcaid"] = matched_wcaid

    # write merged file to disk
    atomic_write(merged_path, merged)

    # also upsert into Supabase wca_ids.lsc_profile
    try:
        _upsert_lsc_profile_to_supabase(merged.get("wcaid"), merged["person"].get("name"), merged)
    except Exception as e:
        logging.warning("Failed to upsert lsc_profile for %s: %s", name, e)

    return merged

# --- Supabase data access helpers ---

def fetch_results_for_round(round_id) -> List[Dict]:
    """
    Fetch round_results for a round.
    Expected columns: competitor_name, attempt1..attempt5, pos, best, average, best_index, worst_index
    """
    resp = supabase.table("round_results").select("*").eq("round_id", round_id).execute()
    if resp.error:
        raise RuntimeError(f"Supabase error fetching results for round {round_id}: {resp.error.message}")
    return resp.data or []

# --- Supabase data access helpers (fixed) ---

def _resp_data_or_raise(resp, context_msg="Supabase request"):
    """
    Helper: return resp.data if present, otherwise raise with useful message.
    Works with different supabase-py response shapes.
    """
    # prefer .data attribute
    data = getattr(resp, "data", None)
    # some versions may return a dict-like object
    if data is None and isinstance(resp, dict):
        data = resp.get("data")
    # check for errors in multiple possible places
    err = getattr(resp, "error", None) or (resp.get("error") if isinstance(resp, dict) else None)
    status = getattr(resp, "status_code", None) or (resp.get("status_code") if isinstance(resp, dict) else None)
    if err:
        raise RuntimeError(f"{context_msg}: {err}")
    # supabase-py may return None data on empty result; normalize to []
    return data or []

def fetch_wca_list_from_supabase() -> List[Dict]:
    """
    Try to fetch a WCA list from a table named 'wca_ids' or 'wca_list'.
    Fallback: return empty list (the script will still create merged files from local WCA cache).
    """
    for tbl in ("wca_ids"):
        try:
            resp = supabase.table(tbl).select("wcaid,name").execute()
        except Exception:
            # table might not exist or permission denied; skip to next
            continue
        try:
            rows = _resp_data_or_raise(resp, f"fetching {tbl}")
        except Exception:
            # skip this table if it errors
            continue
        if rows:
            return [{"wcaid": r.get("wcaid"), "name": r.get("name")} for r in rows]
    return []

def fetch_rounds_for_comp(comp_id) -> List[Dict]:
    """
    Fetch rounds for a competition from rounds table.
    Expected columns: id, comp_id, event_id, round_code, format, advance_to_next, date
    """
    try:
        resp = supabase.table("rounds").select("*").eq("comp_id", comp_id).execute()
    except Exception as e:
        raise RuntimeError(f"Supabase request failed fetching rounds for comp {comp_id}: {e}")
    rows = _resp_data_or_raise(resp, f"fetching rounds for comp {comp_id}")
    return rows

def fetch_results_for_round(round_id) -> List[Dict]:
    """
    Fetch round_results for a round.
    Expected columns: competitor_name, attempt1..attempt5, pos, best, average, best_index, worst_index
    """
    try:
        resp = supabase.table("round_results").select("*").eq("round_id", round_id).execute()
    except Exception as e:
        raise RuntimeError(f"Supabase request failed fetching results for round {round_id}: {e}")
    rows = _resp_data_or_raise(resp, f"fetching round_results for round {round_id}")
    return rows

def fetch_locked_competitions_from_supabase() -> List[Dict]:
    """
    Fetch competitions that are marked as locked from Supabase.
    Tries common name/title columns and returns list of dicts with id and title.
    """
    # Try common column names
    for name_col in ("name", "title"):
        try:
            resp = supabase.table("competitions").select(f"id,{name_col},locked").eq("locked", True).execute()
        except Exception as e:
            raise RuntimeError(f"Supabase error fetching competitions: {e}")
        rows = _resp_data_or_raise(resp, "fetching competitions")
        if rows:
            comps = []
            for r in rows:
                comps.append({
                    "id": r.get("id"),
                    "title": r.get(name_col) or r.get("title") or r.get("name"),
                    "locked": r.get("locked", True)
                })
            return comps
    # If no rows returned, return empty list
    return []

# --- Main processing logic (adapted from original) ---

def process_competition_from_db(comp, rounds_for_comp, wca_list, results_by_name):
    """
    comp: dict with keys id and title
    rounds_for_comp: list of round rows from DB
    wca_list: list of {wcaid, name}
    results_by_name: dict to populate
    """
    comp_id = comp.get("id")
    comp_title = comp.get("title") or comp.get("name") or str(comp_id)

    # Build WCA lookup maps
    # KEY: exact name string as-is (trimmed). No slug/normalization.
    wca_by_name = {}
    wca_by_id = {}
    for w in wca_list:
        name = (w.get("name") or "").strip()
        wcaid = w.get("wcaid")
        if name:
            wca_by_name.setdefault(name, []).append(w)
        if wcaid:
            wca_by_id[str(wcaid)] = w

    # For debugging: collect unmatched names
    unmatched = set()

    # Iterate rounds fetched from DB
    for r in rounds_for_comp:
        event_name = r.get("event_id") or r.get("event") or None
        round_code = r.get("round_code") or r.get("round") or None
        fmt = r.get("format") or None
        round_date = r.get("date") or None

        round_label = round_code

        round_id = r.get("id")
        try:
            rows = fetch_results_for_round(round_id)
        except Exception as e:
            logging.warning("Failed to fetch results for round %s: %s", round_id, e)
            rows = []

        for row in rows:
            pname = (row.get("competitor_name") or "").strip()
            if not pname:
                continue

            matched = None
            matched_wcaid = None

            # 1) Try explicit wcaid field in the row (if present)
            candidate_wcaid = row.get("wcaid") or row.get("wca_id") or row.get("wca")
            if candidate_wcaid:
                candidate_wcaid = str(candidate_wcaid).strip()
                if candidate_wcaid in wca_by_id:
                    matched = wca_by_id[candidate_wcaid]
                    matched_wcaid = candidate_wcaid

            # 2) Exact name match (word-for-word). No slug/normalization.
            if not matched:
                # direct lookup by the exact trimmed name
                if pname in wca_by_name:
                    # if multiple entries share the exact same name, pick the first
                    matched = wca_by_name[pname][0]
                    matched_wcaid = matched.get("wcaid")

            # Build attempts array from attempt1..attempt5 fields
            raw_attempts = []
            for i in range(1, 6):
                key = f"attempt{i}"
                raw_attempts.append(row.get(key))

            parsed_attempts = [parse_attempt_value_from_db(v, event_name) for v in raw_attempts]
            parsed_attempts = (parsed_attempts + [0] * max(0, 5 - len(parsed_attempts)))[:5]

            computed_best, computed_best_index, computed_worst_index = compute_best_and_indices(parsed_attempts, event_name)
            best_val = row.get("best")
            if best_val is None:
                best_val = computed_best
            else:
                best_val = parse_attempt_value_from_db(best_val, event_name)

            avg_val = row.get("average")
            if avg_val is not None:
                avg_val = parse_attempt_value_from_db(avg_val, event_name)

            pos_val = row.get("pos")
            try:
                pos_val = int(pos_val) if pos_val is not None else None
            except Exception:
                pos_val = None

            # Choose canonical name for merged file: prefer matched WCA profile name, else DB name
            canonical_name = pname
            if matched and matched.get("name"):
                canonical_name = matched.get("name")

            result_obj = {
                "pos": pos_val,
                "competition_id": comp_title,
                "event_id": event_name,
                "round_type_id": round_code,
                "format_id": FORMAT_MAP.get(fmt, fmt or None),
                "attempts": parsed_attempts,
                "best": best_val,
                "average": avg_val,
                "best_index": computed_best_index,
                "worst_index": computed_worst_index,
                "round_date": round_date,
            }

            if all(a == 0 for a in parsed_attempts):
                continue

            # Ensure results_by_name uses canonical_name key
            results_by_name.setdefault(canonical_name, {"wcaid": matched_wcaid, "results": []})
            if results_by_name[canonical_name].get("wcaid") is None and matched_wcaid:
                results_by_name[canonical_name]["wcaid"] = matched_wcaid
            results_by_name[canonical_name]["results"].append(result_obj)

            if not matched:
                unmatched.add(pname)

    # Log unmatched sample for debugging
    if unmatched:
        sample = list(unmatched)[:20]
        logging.info("Unmatched competitor names for competition %s (sample %d): %s", comp_title, len(sample), sample)

def main():
    ensure_dir(WCA_CACHE_DIR)

    # 1) Clear existing -merged.json files
    clear_merged_files()

    # 2) Copy WCA profiles into LSC merged files
    copy_wca_profiles_to_merged()

    # 3) Replace competition_id values in merged files using wca_comp_titles cache
    replace_competition_ids_with_names_from_cache()

    # 4) Load WCA list (try Supabase first)
    wca_list = fetch_wca_list_from_supabase()
    if not wca_list:
        # fallback: load from local WCA cache directory (profiles/wca/*.json)
        if os.path.isdir(WCA_CACHE_DIR):
            for fn in os.listdir(WCA_CACHE_DIR):
                if not fn.lower().endswith(".json"):
                    continue
                data = load_json_if_exists(os.path.join(WCA_CACHE_DIR, fn)) or {}
                name = data.get("person", {}).get("name") or data.get("name")
                wcaid = data.get("wcaid")
                if name:
                    wca_list.append({"wcaid": wcaid, "name": name})

    # 5) Fetch locked competitions from Supabase
    try:
        locked_comps = fetch_locked_competitions_from_supabase()
    except Exception as e:
        raise SystemExit(f"Failed to fetch locked competitions from Supabase: {e}")

    if not locked_comps:
        print("No locked competitions found in Supabase (or none returned).")
        return

    print(f"Found {len(locked_comps)} locked competitions to scan")

    results_by_name = {}

    # For each competition, fetch rounds and results and process
    for comp in locked_comps:
        comp_id = comp.get("id")
        comp_title = comp.get("title") or comp.get("name") or str(comp_id)
        print("Scanning competition:", comp_title, "(", comp_id, ")")
        try:
            rounds = fetch_rounds_for_comp(comp_id)
        except Exception as e:
            logging.warning("Failed to fetch rounds for competition %s: %s", comp_title, e)
            rounds = []

        process_competition_from_db(comp, rounds, wca_list, results_by_name)

    # Write merged files
    for name, info in results_by_name.items():
        matched_wcaid = info.get("wcaid")
        lsc_results = info.get("results", [])
        write_merged_for_name(name, matched_wcaid, lsc_results)

    # Ensure everyone in the WCA list has a merged file
    for w in wca_list:
        wca_name = w.get("name")
        wca_id = w.get("wcaid")
        if not wca_name or not wca_id:
            continue
        if wca_name in results_by_name:
            continue
        write_merged_for_name(wca_name, wca_id, [])

    print("Done. Merged results written to", LSC_CACHE_DIR)

if __name__ == "__main__":
    main()
