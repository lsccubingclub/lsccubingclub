#!/usr/bin/env python3
# scripts/prefetch_competitions.py
# Prefetch locked competition data for /competitions view
# Produces data/competitions/{slug}/all.json, persons.json, podiums.json, offered.json, details.json, winners.json
# Each result includes: attempts (5 ints), best, average, best_index, worst_index, pos, round_tab, round_date

import os
import sys
import json
import urllib.parse
import requests
from collections import defaultdict, OrderedDict

MASTER_SHEET_ID = os.environ.get("MASTER_SHEET_ID", "1fjWyEbc7a5A3RjkFm0BEnE_lyHXCa3kYiKRd2IP9j5Q")
API_KEY = os.environ.get("GOOGLE_API_KEY", "AIzaSyA_4BKwiXfv_T9XhbdenwHuGm0k5uc89S8")
OUT_DIR = os.environ.get("OUTPUT_DIR", "data/competitions")

if not API_KEY:
    print("ERROR: GOOGLE_API_KEY not set", file=sys.stderr)
    sys.exit(1)
if not MASTER_SHEET_ID:
    print("ERROR: MASTER_SHEET_ID not set", file=sys.stderr)
    sys.exit(1)

META_TAB = "meta"
META_RANGE = "A2:H500"

ROUND_ORDER_RANK = {"Final": 4, "Third Round": 3, "Second Round": 2, "First Round": 1}

EVENT_CODE_TO_NAME = {
    "333": "3x3x3 Cube","222": "2x2x2 Cube","444": "4x4x4 Cube","555": "5x5x5 Cube","666": "6x6x6 Cube","777": "7x7x7 Cube",
    "333bf": "3x3x3 Blindfolded","333fm": "3x3x3 Fewest Moves","333oh": "3x3x3 One-handed","clock": "Clock","minx": "Megaminx",
    "pyram": "Pyraminx","skewb": "Skewb","sq1": "Square-1","444bf": "4x4x4 Blindfolded","555bf": "5x5x5 Blindfolded","333mbf": "3x3x3 Multi-Blind"
}
EVENT_NAME_TO_CODE = {v.lower(): k for k, v in EVENT_CODE_TO_NAME.items()}

RANK_HEADERS = {"#", "rank", "place", "pos", "position"}

def slug(s: str) -> str:
    return urllib.parse.quote((s or "").strip().replace(" ", "_"))

def http_get_json(url: str, params: dict = None, timeout: int = 30):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

def read_master_meta():
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{MASTER_SHEET_ID}/values/{urllib.parse.quote(META_TAB+'!'+META_RANGE)}"
    params = {"key": API_KEY}
    j = http_get_json(url, params=params)
    rows = (j and j.get("values")) or []
    comps = OrderedDict()
    last_comp = None
    last_sheet = None
    last_locked = False
    for row in rows:
        comp = (row[0] if len(row) > 0 else "").strip()
        sheet = (row[1] if len(row) > 1 else "").strip()
        locked = (row[2] if len(row) > 2 else "").strip().lower()
        event = (row[3] if len(row) > 3 else "").strip()
        round_label = (row[4] if len(row) > 4 else "").strip()
        fmt = (row[5] if len(row) > 5 else "").strip().lower()
        adv = (row[6] if len(row) > 6 else "").strip()
        date = (row[7] if len(row) > 7 else "").strip()

        if not comp and not sheet and last_comp:
            comp = last_comp
            sheet = last_sheet
            locked = 'true' if last_locked else 'false'

        if comp and sheet and comp not in comps:
            comps[comp] = {
                "sheet_id": sheet,
                "locked": locked in ("true", "locked", "yes", "1"),
                "rounds": [],
                "dates": set()
            }

        if comp and event and round_label:
            comps[comp]["rounds"].append({
                "event": event,
                "round": round_label,
                "format": fmt or "ao5",
                "advance": int(adv) if adv and adv.isdigit() else None,
                "date": date or ""
            })
            if date:
                comps[comp]["dates"].add(date)

        last_comp = comp or last_comp
        last_sheet = sheet or last_sheet
        last_locked = (locked in ("true", "locked", "yes", "1")) or last_locked

    for comp, info in comps.items():
        dates = sorted(list(info["dates"]))
        info["date"] = dates[0] if dates else ""
    return comps

def list_sheet_tabs(sheet_id: str):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    params = {"fields": "sheets.properties.title", "key": API_KEY}
    j = http_get_json(url, params=params)
    if not j:
        return []
    return [s.get("properties", {}).get("title") for s in j.get("sheets", []) if s.get("properties", {}).get("title")]

def find_public_tab_names(tab_titles):
    out = [t for t in tab_titles if "public" in t.strip().lower()]
    if not out:
        out = [t for t in tab_titles if "-" in t]
    return out

def fetch_tab_values(sheet_id: str, tab_title: str):
    if not tab_title:
        return []
    safe = tab_title.replace("'", "\\'")
    range_expr = f"'{safe}'!A:Z"
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{urllib.parse.quote(range_expr)}"
    params = {"key": API_KEY, "majorDimension": "ROWS"}
    j = http_get_json(url, params=params)
    return (j and j.get("values")) or []

def headers_and_rows_from_values(values):
    if not values or len(values) == 0:
        return [], []
    headers = [str(h).strip() for h in values[0]]
    rows = values[1:] if len(values) > 1 else []
    dict_rows = []
    for r in rows:
        obj = {}
        for i, h in enumerate(headers):
            obj[h] = r[i] if i < len(r) else ""
        dict_rows.append(obj)
    return headers, dict_rows

def format_final_col_name(fmt: str) -> str:
    fmt = (fmt or "").lower()
    if fmt == "mo3": return "Mean"
    if fmt in ("bo1", "bo2", "bo3"): return "Best"
    if fmt == "ao5": return "Average"
    return "Average"

# ---- REPLACED ATTEMPT HANDLING ----

# Collect attempts only from headers named exactly "1","2","3","4","5"
ATTEMPT_HEADER_NAMES = ["1", "2", "3", "4", "5"]

def parse_attempt_value(cell, event_id):
    """
    Normalize a single attempt cell:
    - empty / None / '' -> 0
    - 'DNF' -> -1
    - 'DNS' -> -2
    - for normal time strings -> centiseconds (int)
    - for FMC events prefer integer moves
    """
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

    # Fewest moves: prefer integer moves if parseable
    if event_id == "333fm":
        try:
            return int(round(float(s)))
        except Exception:
            return 0

    # Time parsing -> seconds to centiseconds
    try:
        if ":" in s:
            mm, rest = s.split(":", 1)
            seconds = int(mm) * 60 + float(rest)
        else:
            seconds = float(s)
        return int(round(seconds * 100))
    except Exception:
        try:
            return int(round(float(s)))
        except Exception:
            return 0

def parse_mean_value(cell, event_id):
    # Return centiseconds for mean-like values
    if cell is None or str(cell).strip() == "":
        return None
    s = str(cell).strip()
    try:
        if ":" in s:
            mm, rest = s.split(":", 1)
            seconds = int(mm) * 60 + float(rest)
        else:
            seconds = float(s)
        return int(round(seconds * 100))
    except Exception:
        try:
            return int(float(s))
        except Exception:
            return None

def collect_attempts_from_row(row, headers, event_id):
    """
    Read attempts only from headers exactly '1'..'5'.
    If a header missing -> 0.
    Returns list of 5 integers.
    """
    attempts = []
    header_map = { (h or "").strip(): h for h in headers }
    for attempt_name in ATTEMPT_HEADER_NAMES:
        hdr = header_map.get(attempt_name)
        if hdr is None:
            attempts.append(0)
        else:
            raw = row.get(hdr, "")
            attempts.append(parse_attempt_value(raw, event_id))
    return attempts

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

def build_offered_from_meta(comp_info):
    by_event = defaultdict(list)
    advances = {}

    for r in comp_info.get("rounds", []):
        ev = r.get("event")
        rn = r.get("round")
        fmt = (r.get("format") or "ao5").lower()
        adv = r.get("advance")
        date = r.get("date") or comp_info.get("date") or ""

        if not ev or not rn:
            continue

        round_entry = {
            "round": rn,
            "format": fmt,
            "date": date,
            "advance": adv
        }
        if round_entry not in by_event[ev]:
            by_event[ev].append(round_entry)

        advances[f"{ev} - {rn}"] = adv

    for ev in list(by_event.keys()):
        def rank_key(item):
            rname = item.get("round", "")
            return ROUND_ORDER_RANK.get(rname, 0)
        by_event[ev].sort(key=rank_key)

    return {"byEvent": by_event, "advance": advances}

def choose_tab_for_round(sheet_values_by_tab, event, round_label):
    key_frag = f"{event} - {round_label}".strip().lower()
    for t in sheet_values_by_tab.keys():
        if key_frag in t.lower():
            return t
    for t in sheet_values_by_tab.keys():
        tl = t.lower()
        if event.lower() in tl and round_label.lower() in tl:
            return t
    for t in sheet_values_by_tab.keys():
        if event.lower() in t.lower():
            return t
    return None

def build_all_json(comp_info, sheet_values_by_tab):
    by_event = defaultdict(list)
    for r in comp_info["rounds"]:
        ev = r["event"]; rn = r["round"]; fmt = r.get("format", "ao5")
        final_col = format_final_col_name(fmt)
        chosen_tab = choose_tab_for_round(sheet_values_by_tab, ev, rn)
        vals = sheet_values_by_tab.get(chosen_tab)
        if not vals:
            by_event[ev].append({"round": rn, "rows": []})
            continue
        headers, dict_rows = headers_and_rows_from_values(vals)
        avg_hdr = None
        for h in headers:
            if isinstance(h, str) and h.strip().lower() == "mean":
                avg_hdr = h; break
        if not avg_hdr:
            for h in headers:
                if isinstance(h, str) and h.strip().lower() == "average":
                    avg_hdr = h; break
        event_id = EVENT_NAME_TO_CODE.get(ev.lower())
        pack = []
        for row in dict_rows:
            name = (row.get("Name") or row.get("name") or "").strip()
            if not name:
                continue
            attempts = collect_attempts_from_row(row, headers, event_id)
            best, best_index, worst_index = compute_best_and_indices(attempts, event_id)
            best_val = None
            if "Best" in headers:
                v = row.get("Best")
                if v not in (None, ""):
                    best_val = parse_attempt_value(v, event_id)
            best_out = best_val if best_val is not None else best
            pos = None
            if "#" in headers:
                v = row.get("#")
                if v not in (None, "") and str(v).strip().isdigit():
                    pos = int(str(v).strip())
            avg_val = parse_mean_value((row.get(final_col) or "").strip(), event_id)
            pack.append({
                "name": name,
                "attempts": attempts,
                "best": best_out,
                "average": avg_val,
                "best_index": best_index,
                "worst_index": worst_index,
                "pos": pos
            })
        by_event[ev].append({"round": rn, "rows": pack})
    return {"events": by_event}

def build_persons_json(comp_info, sheet_values_by_tab):
    name_set = set()
    persons_map = {}
    for t, vals in sheet_values_by_tab.items():
        headers, dict_rows = headers_and_rows_from_values(vals)
        for row in dict_rows:
            n = (row.get("Name") or row.get("name") or "").strip()
            if n:
                name_set.add(n)
    for r in comp_info["rounds"]:
        ev = r["event"]; rn = r["round"]; fmt = r.get("format", "ao5")
        chosen_tab = choose_tab_for_round(sheet_values_by_tab, ev, rn)
        vals = sheet_values_by_tab.get(chosen_tab)
        if not vals:
            continue
        headers, dict_rows = headers_and_rows_from_values(vals)
        event_id = EVENT_NAME_TO_CODE.get(ev.lower())
        avg_hdr = None
        for h in headers:
            if isinstance(h, str) and h.strip().lower() == "mean":
                avg_hdr = h; break
        if not avg_hdr:
            for h in headers:
                if isinstance(h, str) and h.strip().lower() == "average":
                    avg_hdr = h; break
        for row in dict_rows:
            name = (row.get("Name") or row.get("name") or "").strip()
            if not name:
                continue
            attempts = collect_attempts_from_row(row, headers, event_id)
            best, best_index, worst_index = compute_best_and_indices(attempts, event_id)
            avg_val = None
            if avg_hdr:
                v = row.get(avg_hdr)
                if v not in (None, ""):
                    avg_val = parse_mean_value(v, event_id)
            result_obj = {
                "event": ev,
                "round": rn,
                "attempts": attempts,
                "best": best,
                "average": avg_val,
                "best_index": best_index,
                "worst_index": worst_index,
                "pos": (int(row.get("#")) if "#" in headers and row.get("#") and str(row.get("#")).strip().isdigit() else None)
            }
            persons_map.setdefault(name, []).append(result_obj)
    persons = [{"name": n, "results": persons_map.get(n, [])} for n in sorted(persons_map.keys(), key=lambda s: s.lower())]
    return {"persons": persons}

def build_podiums_json(comp_info, sheet_values_by_tab):
    by_event = {}
    for r in comp_info["rounds"]:
        ev = r["event"]; rn = r["round"]; fmt = r.get("format", "ao5")
        if not (rn.lower().strip().endswith("final") or "final" in rn.lower()):
            continue
        chosen_tab = choose_tab_for_round(sheet_values_by_tab, ev, rn)
        vals = sheet_values_by_tab.get(chosen_tab)
        if not vals:
            continue
        headers, dict_rows = headers_and_rows_from_values(vals)
        event_id = EVENT_NAME_TO_CODE.get(ev.lower())
        pack = []
        for row in dict_rows:
            name = (row.get("Name") or row.get("name") or "").strip()
            if not name:
                continue
            attempts = collect_attempts_from_row(row, headers, event_id)
            best, best_index, worst_index = compute_best_and_indices(attempts, event_id)
            
            pack.append({
                "name": name,
                "attempts": attempts,
                "best": best,
                "average": parse_mean_value((row.get(format_final_col_name(fmt)) or "").strip(), event_id),
                "best_index": best_index,
                "worst_index": worst_index
            })
        if pack:
            by_event[ev] = pack[:3]
    return {"events": by_event}

def build_winners_from_podiums(podiums_json):
    winners = {}
    for ev, arr in (podiums_json.get("events") or {}).items():
        if arr and len(arr) > 0:
            top = arr[0]
            winners[ev] = {
                "name": top.get("name", ""), 
                "attempts": top.get("attempts", ""),
                "best": top.get("best", ""),
                "average": top.get("average", ""),
                "best_index": top.get("best_index", ""),
                "worst_index": top.get("worst_index", "")}
    return winners

def read_details_tab(sheet_id: str):
    vals = fetch_tab_values(sheet_id, "Details")
    if not vals:
        for t in ("details", "Details ", "DETAILS"):
            vals = fetch_tab_values(sheet_id, t)
            if vals:
                break
    if not vals:
        return {}
    data = {}
    for row in vals:
        if not row:
            continue
        key = (row[0] if len(row) > 0 else "").strip().lower()
        val = (row[1] if len(row) > 1 else "").strip()
        if key:
            data[key] = val
    return data

def prefetch_all_locked():
    comps = read_master_meta()
    os.makedirs(OUT_DIR, exist_ok=True)
    for comp_name, comp_info in comps.items():
        if not comp_info.get("locked"):
            continue
        sheet_id = comp_info.get("sheet_id") or MASTER_SHEET_ID
        print(f"Processing locked competition: {comp_name} (sheet: {sheet_id})", file=sys.stderr)

        tabs = list_sheet_tabs(sheet_id)
        public_tabs = find_public_tab_names(tabs)
        if not public_tabs:
            public_tabs = [t for t in tabs if "-" in t]

        sheet_values_by_tab = {}
        for t in public_tabs:
            vals = fetch_tab_values(sheet_id, t)
            if vals:
                sheet_values_by_tab[t] = vals

        offered_json = build_offered_from_meta(comp_info)
        all_json = build_all_json(comp_info, sheet_values_by_tab)
        persons_json = build_persons_json(comp_info, sheet_values_by_tab)
        podiums_json = build_podiums_json(comp_info, sheet_values_by_tab)
        winners_json = build_winners_from_podiums(podiums_json)

        details = read_details_tab(sheet_id)
        if not details.get("date"):
            details["date"] = comp_info.get("date", "")
        details_out = {
            "venue": details.get("venue", ""),
            "date": details.get("date", ""),
            "main_event": details.get("main_event", "") or "",
            "podiums": {}
        }
        if details_out["main_event"]:
            details_out["podiums"][details_out["main_event"]] = podiums_json.get("events", {}).get(details_out["main_event"], [])

        comp_slug = slug(comp_name)
        comp_dir = os.path.join(OUT_DIR, comp_slug)
        os.makedirs(comp_dir, exist_ok=True)
        with open(os.path.join(comp_dir, "offered.json"), "w", encoding="utf-8") as f:
            json.dump(offered_json, f, ensure_ascii=False)
        with open(os.path.join(comp_dir, "all.json"), "w", encoding="utf-8") as f:
            json.dump(all_json, f, ensure_ascii=False)
        with open(os.path.join(comp_dir, "persons.json"), "w", encoding="utf-8") as f:
            json.dump(persons_json, f, ensure_ascii=False)
        with open(os.path.join(comp_dir, "podiums.json"), "w", encoding="utf-8") as f:
            json.dump(podiums_json, f, ensure_ascii=False)
        with open(os.path.join(comp_dir, "details.json"), "w", encoding="utf-8") as f:
            json.dump(details_out, f, ensure_ascii=False)
        with open(os.path.join(comp_dir, "winners.json"), "w", encoding="utf-8") as f:
            json.dump(winners_json, f, ensure_ascii=False)

        print(f"Wrote data for {comp_name} -> {comp_dir}", file=sys.stderr)

if __name__ == "__main__":
    prefetch_all_locked()
