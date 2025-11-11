#merge_lsc.py
"""
merge_lsc.py

Reads unified meta tab (meta!A2:H999) from the master sheet, inspects ONLY the public tabs
named exactly "{event} - {round}public" in each competition spreadsheet, normalizes results,
and writes/updates profiles/lsc/{nameslug}-merged.json. It merges with any existing
profiles/wca/{nameslug}.json if present.

Meta parsing groups rows under the last seen Competition name to include subsequent rounds
listed below it (blank Competition cells).
"""

import os
import json
import re
import tempfile
import time
import logging
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

from google.oauth2 import service_account
from googleapiclient.discovery import build
import config

# Config / constants
WCA_CACHE_DIR = getattr(config, "WCA_CACHE_DIR", "profiles/wca")
LSC_CACHE_DIR = getattr(config, "LSC_CACHE_DIR", "profiles/lsc")
MASTER_ID = config.SHEETS_MASTER_ID
CREDENTIAL_FILE = config.GOOGLE_CREDENTIAL_FILE

# If you keep WCA title caching, leave these. They aren't critical to LSC-only merges.
WCA_TITLE_CACHE_PATH = os.environ.get("WCA_TITLE_CACHE", "data/wca_comp_titles.json")
WCA_FETCH_USER_AGENT = os.environ.get("WCA_FETCH_USER_AGENT", "merge_lsc/1.0 (+https://example.org)")
WCA_FETCH_RETRIES = int(os.environ.get("WCA_FETCH_RETRIES", "3"))
WCA_FETCH_BACKOFF = float(os.environ.get("WCA_FETCH_BACKOFF", "1.0"))
WCA_TIMEOUT = float(os.environ.get("WCA_TIMEOUT", "10"))

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

# Sheets API
creds = service_account.Credentials.from_service_account_file(
    CREDENTIAL_FILE,
    scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
)
sheets_svc = build("sheets", "v4", credentials=creds).spreadsheets()

# Helpers
def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def read_values(spreadsheet_id, rng):
    try:
        resp = sheets_svc.values().get(spreadsheetId=spreadsheet_id, range=rng).execute()
        return resp.get("values", [])
    except Exception:
        return []

def spreadsheet_metadata(spreadsheet_id):
    return sheets_svc.get(spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()

def name_to_slug(name):
    s = (name or "").strip().lower()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'-{2,}', '-', s)
    s = s.strip('-')
    return s or "unknown"

def slugify_name_for_lookup(s):
    return re.sub(r'[^a-z0-9]+','-', (s or "").lower()).strip('-')

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

def parse_attempt_value(cell, event_id):
    if cell is None:
        return 0
    s = str(cell).strip()
    if s == "":
        return 0
    su = s.upper()
    if su == "DNF":
        return -1
    if su == "DNS":
        return -2
    if event_id == "333fm":
        try:
            return int(float(s))
        except Exception:
            return 0
    try:
        if ":" in s:
            mm, rest = s.split(":", 1)
            seconds = int(mm) * 60 + float(rest)
        else:
            seconds = float(s)
        return int(round(seconds * 100))
    except Exception:
        return 0

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

def find_header_index(headers, name_variants):
    hl = [str(h).strip().lower() if h is not None else "" for h in headers]
    for v in name_variants:
        vlow = v.lower()
        if vlow in hl:
            return hl.index(vlow)
    return None

def is_public_tab(tab_name):
    if not tab_name:
        return False
    return tab_name.strip().lower().endswith("public")

def result_fingerprint(r):
    return (
        str(r.get("competition_id") or "") + "|" +
        str(r.get("round_tab") or r.get("round_name") or "") + "|" +
        str(r.get("event_id") or "") + "|" +
        str(r.get("pos") or "") + "|" +
        str(r.get("round_date") or "")
    )

def wca_profile_path_for_name(name):
    return os.path.join(WCA_CACHE_DIR, f"{name_to_slug(name)}.json")

def merged_path_for_name(name):
    return os.path.join(LSC_CACHE_DIR, f"{name_to_slug(name)}-merged.json")

def load_json_if_exists(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

# --- WCA competition title cache (optional, kept as-is) ---
def _load_wca_title_cache() -> Dict[str, str]:
    try:
        with open(WCA_TITLE_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_wca_title_cache(cache: Dict[str, str]) -> None:
    try:
        ensure_dir(os.path.dirname(WCA_TITLE_CACHE_PATH) or ".")
        with open(WCA_TITLE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("Failed saving WCA title cache: %s", e)

def fetch_wca_competition_name(comp_id: str, session: Optional[requests.Session] = None) -> Optional[str]:
    if not comp_id:
        return None
    url = f"https://www.worldcubeassociation.org/competitions/{comp_id}"
    s = session or requests.Session()
    headers = {"User-Agent": WCA_FETCH_USER_AGENT}
    last_err = None
    for attempt in range(1, WCA_FETCH_RETRIES + 1):
        try:
            resp = s.get(url, headers=headers, timeout=WCA_TIMEOUT)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            html = resp.text
            soup = BeautifulSoup(html, "html.parser")
            title = (soup.title.string or "").strip() if soup.title else ""
            if not title:
                return None
            name = title.split(" | ")[0].strip() if " | " in title else title.strip()
            return name or None
        except Exception as e:
            last_err = e
            time.sleep(WCA_FETCH_BACKOFF * (2 ** (attempt - 1)))
    logging.warning("Failed to fetch WCA competition title for %s: %s", comp_id, last_err)
    return None

def refresh_comp_titles_for_comps(comps):
    cache = _load_wca_title_cache()
    session = requests.Session()
    comp_ids = set()
    for c in comps:
        cid = c.get("title")
        if not isinstance(cid, str) or not cid:
            continue
        if " " not in cid and any(ch.isalnum() for ch in cid):
            if cid not in cache:
                comp_ids.add(cid)

    for cid in sorted(comp_ids):
        try:
            name = fetch_wca_competition_name(cid, session=session)
            cache[cid] = name if name else cid
        except Exception as e:
            logging.warning("Error fetching competition %s: %s", cid, e)
            cache[cid] = cid
        _save_wca_title_cache(cache)
        time.sleep(0.2)

    for c in comps:
        cid = c.get("title")
        if not isinstance(cid, str) or not cid:
            continue
        if " " not in cid and any(ch.isalnum() for ch in cid):
            c["title"] = cache.get(cid, cid)
    _save_wca_title_cache(cache)
    return comps

def refresh_comp_titles_in_wca_profiles(wca_profiles_dir=WCA_CACHE_DIR):
    cache = _load_wca_title_cache()
    session = requests.Session()
    comp_ids = set()
    profile_paths = []
    if not os.path.isdir(wca_profiles_dir):
        return
    for fn in os.listdir(wca_profiles_dir):
        if not fn.lower().endswith(".json"):
            continue
        profile_paths.append(os.path.join(wca_profiles_dir, fn))
    for path in profile_paths:
        data = load_json_if_exists(path)
        if not data:
            continue
        results = data.get("results") or []
        for r in results:
            cid = r.get("competition_id")
            if not isinstance(cid, str) or not cid:
                continue
            if " " not in cid and any(ch.isalnum() for ch in cid):
                if cid not in cache:
                    comp_ids.add(cid)

    for cid in sorted(comp_ids):
        try:
            name = fetch_wca_competition_name(cid, session=session)
            cache[cid] = name if name else cid
        except Exception as e:
            logging.warning("Error fetching competition %s: %s", cid, e)
            cache[cid] = cid
        _save_wca_title_cache(cache)
        time.sleep(0.2)

    for path in profile_paths:
        data = load_json_if_exists(path)
        if not data:
            continue
        modified = False
        results = data.get("results") or []
        for r in results:
            cid = r.get("competition_id")
            if not isinstance(cid, str) or not cid:
                continue
            if " " not in cid and any(ch.isalnum() for ch in cid):
                new = cache.get(cid, cid)
                if new != cid:
                    r["competition_id"] = new
                    modified = True
        if modified:
            try:
                atomic_write(path, data)
            except Exception as e:
                logging.warning("Failed writing updated profile %s: %s", path, e)
    _save_wca_title_cache(cache)

# --- Main merge helpers ---
def write_merged_for_name(name, matched_wcaid, lsc_results):
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
        fp = result_fingerprint(r)
        if fp not in existing_fps:
            merged["results"].append(r)
            existing_fps.add(fp)

    if merged.get("wcaid") is None and matched_wcaid:
        merged["wcaid"] = matched_wcaid

    atomic_write(merged_path, merged)

# --- Competition processing ---
def process_competition(comp, rounds_for_comp, wca_list, results_by_name):
    sheet_id = comp.get("sheet_id")
    if not sheet_id:
        return
    try:
        meta = spreadsheet_metadata(sheet_id)
    except Exception as e:
        print("Failed to load spreadsheet metadata for", comp.get("title"), e)
        return

    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if not tabs:
        return

    # Build WCA lookup maps
    wca_by_name = {slugify_name_for_lookup(w["name"]): w for w in wca_list if w.get("name")}
    wca_by_id = {w["wcaid"]: w for w in wca_list if w.get("wcaid")}

    # Iterate meta rounds and target the exact "{event} - {round}public" tab
    for rmeta in (rounds_for_comp or []):
        event_name = (rmeta.get("event") or "").strip()
        round_label = (rmeta.get("round_label") or "").strip()
        full_round = rmeta.get("round") or ""
        round_date = rmeta.get("date") or None
        fmt = (rmeta.get("format") or "").strip().lower()

        if not event_name and " - " in full_round:
            # derive event/round_label from full_round if needed
            parts = full_round.split(" - ", 1)
            event_name = parts[0].strip()
            round_label = parts[1].strip()
        elif not round_label and " - " in full_round:
            round_label = full_round.split(" - ", 1)[1].strip()

        public_tab = f"{event_name} - {round_label}public" if event_name and round_label else None
        if not public_tab or public_tab not in tabs:
            # strict requirement: only fetch from the exact public tab
            continue

        # Read the tab
        values = read_values(sheet_id, f"'{public_tab}'!A:Z")
        if not values or len(values) < 2:
            continue
        headers = [str(h).strip() for h in values[0]]
        rows = values[1:]

        # Header indices
        name_idx = find_header_index(headers, ["name", "full name"])
        if name_idx is None:
            # fallback: any column containing "name"
            for i, h in enumerate(headers):
                if h and "name" in str(h).lower():
                    name_idx = i
                    break
        if name_idx is None:
            continue

        attempt_indices = []
        for i, h in enumerate(headers):
            hu = str(h).strip().upper()
            if hu in ('1','2','3','4','5','A1','A2','A3','A4','A5','ATTEMPT 1','ATTEMPT 2','ATTEMPT1','ATTEMPT2'):
                attempt_indices.append(i)
        # dedupe and limit to 5
        seen = set()
        attempt_indices_clean = []
        for c in attempt_indices:
            if c not in seen:
                seen.add(c)
                attempt_indices_clean.append(c)
        attempt_indices = attempt_indices_clean[:5]

        best_idx = find_header_index(headers, ["best","best time"])
        avg_idx = find_header_index(headers, ["average","avg","mean"])
        rank_idx = find_header_index(headers, ["#","rank","position"])
        event_col_idx = find_header_index(headers, ["event"])
        wcaid_idx = None
        for i, h in enumerate(headers):
            if h and "wca" in str(h).lower():
                wcaid_idx = i
                break

        # Meta-derived fields
        round_name = f"{event_name} - {round_label}" if event_name and round_label else full_round or public_tab.replace("public","").strip()
        format_id = FORMAT_MAP.get(fmt, fmt or None)
        # round type id from round_label
        round_type_id = ROUND_TYPE_MAP.get(round_label, (round_label[:1].lower() if round_label else None))
        # event id from event_name (case-insensitive)
        event_id = None
        if event_name:
            ev_key = event_name.strip().lower()
            event_id = next((code for name, code in EVENT_NAME_TO_CODE.items() if name.lower() == ev_key), None)

        for row in rows:
            pname = (row[name_idx].strip() if name_idx is not None and name_idx < len(row) and row[name_idx] else "").strip()
            if not pname:
                continue
            normalized = slugify_name_for_lookup(pname)
            matched = None
            matched_wcaid = None

            if wcaid_idx is not None and wcaid_idx < len(row) and row[wcaid_idx]:
                candidate = str(row[wcaid_idx]).strip()
                if candidate in wca_by_id:
                    matched = wca_by_id[candidate]
                    matched_wcaid = candidate
            if not matched and normalized in wca_by_name:
                matched = wca_by_name[normalized]
                matched_wcaid = matched.get("wcaid")

            if not matched:
                # skip unknown person
                continue

            raw_attempts = [row[i] if i < len(row) else None for i in attempt_indices]
            while len(raw_attempts) < 5:
                raw_attempts.append(None)

            ev_for_row = event_id
            # fallback: try event column in sheet
            if ev_for_row is None and event_col_idx is not None and event_col_idx < len(row):
                ev_cell = row[event_col_idx]
                if ev_cell:
                    ev_key = str(ev_cell).strip().lower()
                    ev_for_row = next((code for name, code in EVENT_NAME_TO_CODE.items() if name.lower() == ev_key), None)

            parsed_attempts = [parse_attempt_value(v, ev_for_row) for v in raw_attempts]
            parsed_attempts = [int(x) for x in parsed_attempts]
            parsed_attempts = (parsed_attempts + [0] * max(0, 5 - len(parsed_attempts)))[:5]

            sheet_best = None
            if best_idx is not None and best_idx < len(row) and row[best_idx] not in (None, ""):
                sheet_best = parse_attempt_value(row[best_idx], ev_for_row)

            sheet_avg = None
            if avg_idx is not None and avg_idx < len(row) and row[avg_idx] not in (None, ""):
                raw_avg = row[avg_idx]
                if ev_for_row == "333fm":
                    s = str(raw_avg).strip()
                    try:
                        if ":" in s:
                            mm, rest = s.split(":", 1)
                            seconds = int(mm) * 60 + float(rest)
                        else:
                            seconds = float(s)
                        sheet_avg = int(round(seconds * 100))
                    except Exception:
                        try:
                            sheet_avg = int(float(s)) * 100
                        except Exception:
                            sheet_avg = None
                else:
                    sheet_avg = parse_attempt_value(raw_avg, ev_for_row)

            computed_best, computed_best_index, computed_worst_index = compute_best_and_indices(parsed_attempts, ev_for_row)
            best_val = sheet_best if sheet_best is not None else computed_best
            try:
                best_index = parsed_attempts.index(best_val) if best_val is not None else None
            except ValueError:
                best_index = computed_best_index
            worst_index = computed_worst_index
            avg_val = sheet_avg if sheet_avg is not None else None

            pos_val = None
            if rank_idx is not None and rank_idx < len(row) and row[rank_idx] not in (None, ""):
                try:
                    pos_val = int(str(row[rank_idx]).strip())
                except Exception:
                    pos_val = None

            result_obj = {
                "pos": pos_val,
                "competition_id": comp.get("title"),
                "event_id": ev_for_row,
                "round_type_id": round_type_id,
                "format_id": format_id,
                "attempts": parsed_attempts,
                "best": best_val,
                "average": avg_val,
                "best_index": best_index,
                "worst_index": worst_index,
                "round_tab": public_tab,    # exact tab with "public"
                "round_date": round_date,
            }

            if all(a == 0 for a in parsed_attempts):
                continue

            results_by_name.setdefault(pname, {"wcaid": matched_wcaid, "results": []})
            results_by_name[pname]["results"].append(result_obj)

def main():
    ensure_dir(WCA_CACHE_DIR)
    # Optional: refresh WCA competition titles in existing profile files
    try:
        refresh_comp_titles_in_wca_profiles(WCA_CACHE_DIR)
    except Exception as e:
        logging.warning("Failed to refresh competition titles in WCA profiles: %s", e)

    # Read unified meta tab
    meta_rows = read_values(MASTER_ID, "meta!A2:H999")

    # Group meta rows under the last seen competition (to include subsequent blank-comp rows)
    comps_map = {}
    round_lookup = {}
    last_comp = None
    last_sheet_id = None
    last_locked = False

    for row in meta_rows:
        # columns: comp | sheet_id | isLocked? | event | round | format | advance | date
        comp = (row[0].strip() if len(row) > 0 and row[0] else "")
        sheet_id = (row[1].strip() if len(row) > 1 and row[1] else "")
        locked_raw = (row[2].strip() if len(row) > 2 and row[2] else "")
        locked = (locked_raw.upper() == "TRUE")
        event = (row[3].strip() if len(row) > 3 and row[3] else "")
        round_label = (row[4].strip() if len(row) > 4 and row[4] else "")
        fmt = (row[5].strip().lower() if len(row) > 5 and row[5] else "")
        advance = (row[6].strip() if len(row) > 6 and row[6] else "")
        date = (row[7].strip() if len(row) > 7 and row[7] else "")

        # If comp and sheet_id are blank, inherit from last seen competition
        if not comp and not sheet_id and last_comp and last_sheet_id:
            comp = last_comp
            sheet_id = last_sheet_id
            locked = last_locked

        # Ignore rows without a usable competition context
        if not comp or not sheet_id:
            continue

        # If this is a new competition definition row, record it
        if comp not in comps_map:
            comps_map[comp] = {"title": comp, "sheet_id": sheet_id, "is_locked": locked}

        # Remember as current block context
        last_comp = comp
        last_sheet_id = sheet_id
        last_locked = locked

        # Build a clean "Event - Round" string and also store event/round_label explicitly
        full_round = f"{event} - {round_label}" if event and round_label else (event or round_label)
        round_lookup.setdefault(comp, []).append({
            "comp": comp,
            "sheet_id": sheet_id,
            "round": full_round,
            "event": event,
            "round_label": round_label,
            "format": fmt,
            "advance": advance,
            "date": date
        })

    comps = list(comps_map.values())

    # Optional: replace titles for WCA IDs
    try:
        refresh_comp_titles_for_comps(comps)
    except Exception as e:
        logging.warning("Failed to refresh competition titles for master comps: %s", e)

    # Load WCA list
    wca_list = []
    wca_rows = read_values(MASTER_ID, "WCAIDs!A1:D200")
    if wca_rows:
        hdr = wca_rows[0]
        wca_col = next((i for i, h in enumerate(hdr) if isinstance(h, str) and "wcaid" in h.lower()), None)
        name_col = next((i for i, h in enumerate(hdr) if isinstance(h, str) and "name" == str(h).strip().lower()), None)
        for r in wca_rows[1:]:
            name = r[name_col].strip() if (name_col is not None and name_col < len(r) and r[name_col]) else ""
            wcaid = (r[wca_col].strip() if (wca_col is not None and wca_col < len(r) and r[wca_col]) else None)
            if name:
                wca_list.append({"wcaid": wcaid, "name": name})

    # Only scan locked competitions
    locked_comps = [c for c in comps if c.get("is_locked") and c.get("sheet_id")]
    if not locked_comps:
        print("No locked competitions found in meta")
        return

    print(f"Found {len(locked_comps)} locked competitions to scan")

    global results_by_name
    results_by_name = {}

    for comp in locked_comps:
        comp_title = comp.get("title")
        rm_for_comp = round_lookup.get(comp_title, [])
        print("Scanning competition:", comp_title, "(", comp.get("sheet_id"), ") with", len(rm_for_comp), "meta entries")
        process_competition(comp, rm_for_comp, wca_list, results_by_name)

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
