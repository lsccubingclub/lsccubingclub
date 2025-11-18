#!/usr/bin/env python3
import os
import json
import sys
import time
import urllib.parse
import requests
from collections import defaultdict, OrderedDict

MASTER_SHEET_ID = os.environ.get("MASTER_SHEET_ID", "1fjWyEbc7a5A3RjkFm0BEnE_lyHXCa3kYiKRd2IP9j5Q")
API_KEY = os.environ.get("API_KEY", os.environ.get("AIzaSyA_4BKwiXfv_T9XhbdenwHuGm0k5uc89S8", ""))
OUT_DIR = os.environ.get("OUTPUT_DIR", "data/competitions")

META_TAB = "meta"
META_RANGE = "A2:H500"

WCA_EVENTS_ORDER = [
    "3x3x3 Cube","2x2x2 Cube","4x4x4 Cube","5x5x5 Cube","6x6x6 Cube","7x7x7 Cube",
    "3x3x3 Blindfolded","3x3x3 Fewest Moves","3x3x3 One-handed","Clock","Megaminx",
    "Pyraminx","Skewb","Square-1","4x4x4 Blindfolded","5x5x5 Blindfolded","3x3x3 Multi-Blind"
]

EVENT_CODE_TO_NAME = {
    "333":"3x3x3 Cube","222":"2x2x2 Cube","444":"4x4x4 Cube","555":"5x5x5 Cube","666":"6x6x6 Cube","777":"7x7x7 Cube",
    "333bf":"3x3x3 Blindfolded","333fm":"3x3x3 Fewest Moves","333oh":"3x3x3 One-handed",
    "clock":"Clock","minx":"Megaminx","pyram":"Pyraminx","skewb":"Skewb","sq1":"Square-1",
    "444bf":"4x4x4 Blindfolded","555bf":"5x5x5 Blindfolded","333mbf":"3x3x3 Multi-Blind"
}
EVENT_NAME_TO_CODE = {v.lower(): k for k, v in EVENT_CODE_TO_NAME.items()}

ROUND_ORDER_RANK = {"Final": 4, "Third Round": 3, "Second Round": 2, "First Round": 1}

def slug(s: str) -> str:
    return urllib.parse.quote((s or "").strip().replace(" ", "_"))

def fetch_sheet_values(sheet_id: str, ranges: list) -> dict:
    """Batch-get ranges; returns dict tab_name -> rows (list of lists)."""
    out = {}
    if not ranges:
        return out
    params = "&".join([f"ranges={urllib.parse.quote(r)}" for r in ranges])
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values:batchGet?{params}&majorDimension=ROWS&key={API_KEY}"
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        return out
    body = r.json()
    for vr in body.get("valueRanges", []):
        tab = (vr.get("range") or "").split("!")[0].strip().strip("'")
        out[tab] = vr.get("values") or []
    return out

def read_meta():
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{MASTER_SHEET_ID}/values/{urllib.parse.quote(META_TAB+'!'+META_RANGE)}?key={API_KEY}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    rows = r.json().get("values", []) or []
    comps = OrderedDict()
    # columns: 0 comp, 1 sheetId, 2 isLocked, 3 event, 4 round, 5 format, 6 advance, 7 date
    last_comp = None
    last_sheet = None
    last_locked = False
    for row in rows:
        comp = (row[0] if len(row)>0 else "").strip()
        sheet = (row[1] if len(row)>1 else "").strip()
        locked = (row[2] if len(row)>2 else "").strip().lower()
        event = (row[3] if len(row)>3 else "").strip()
        round_label = (row[4] if len(row)>4 else "").strip()
        fmt = (row[5] if len(row)>5 else "").strip().lower()
        adv = (row[6] if len(row)>6 else "").strip()
        date = (row[7] if len(row)>7 else "").strip()

        if not comp and not sheet and last_comp:
            comp = last_comp
            sheet = last_sheet
            locked = 'true' if last_locked else 'false'

        if comp and sheet and comp not in comps:
            comps[comp] = {
                "sheet_id": sheet,
                "locked": locked in ("true","locked","yes","1"),
                "rounds": [],  # list of dicts
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
        last_locked = (locked in ("true","locked","yes","1")) or last_locked

    # compute canonical date per comp (earliest)
    for comp, info in comps.items():
        info["date"] = sorted(list(info["dates"]), key=lambda d: time.strptime(d, "%Y-%m-%d") if d else time.gmtime(0))[0] if info["dates"] else ""
    return comps

def tab_candidates(event_name: str, round_label: str):
    base = f"{event_name} - {round_label}"
    return [f"{base}public", f"{base} public", base]

def format_final_col_name(fmt: str) -> str:
    fmt = (fmt or "").lower()
    if fmt == "mo3": return "Mean"
    if fmt in ("bo1","bo2","bo3"): return "Best"
    return "Average"

def parse_rows(values):
    if not values or len(values) < 1:
        return [], []
    headers = values[0]
    body = values[1:]
    # produce dict rows keyed by headers
    rows = []
    for r in body:
        obj = {}
        for i, h in enumerate(headers):
            obj[h] = r[i] if i < len(r) else ""
        rows.append(obj)
    return headers, rows

def final_value(row, final_col):
    v = (row.get(final_col, "") or "").strip()
    return v

def build_all(comp_name: str, comp_info: dict, sheet_values: dict):
    # events -> [rounds]
    by_event_rounds = defaultdict(list)
    for r in comp_info["rounds"]:
        event = r["event"]; rn = r["round"]; fmt = r["format"]
        # select tab values
        vals = None
        for cand in tab_candidates(event, rn):
            vals = sheet_values.get(cand)
            if vals: break
        if not vals:  # skip if missing
            continue
        headers, rows = parse_rows(vals)
        final_col = format_final_col_name(fmt)
        # rank rows best->worst using final column numeric descending/ascending?
        # We show best (smallest time or largest FMC better?). For consistency, we sort ascending numeric when possible.
        def parse_num(x):
            try:
                return float(x)
            except:
                return float('inf')
        sorted_rows = sorted(rows, key=lambda row: parse_num(final_value(row, final_col)))
        # keep [name, final] minimal view
        pack = [{"name": (row.get("Name") or row.get("name") or "").strip(), "final": final_value(row, final_col)} for row in sorted_rows if (row.get("Name") or row.get("name"))]
        by_event_rounds[event].append({"round": rn, "rows": pack})
    # Order rounds Final->Third->Second->First
    for ev in list(by_event_rounds.keys()):
        by_event_rounds[ev].sort(key=lambda rr: ROUND_ORDER_RANK.get(rr["round"], 0), reverse=True)
    return {"events": by_event_rounds}

def build_persons(comp_name: str, comp_info: dict, sheet_values: dict):
    # person -> list of results
    per = defaultdict(list)
    for r in comp_info["rounds"]:
        event = r["event"]; rn = r["round"]; fmt = r["format"]
        vals = None
        for cand in tab_candidates(event, rn):
            vals = sheet_values.get(cand)
            if vals: break
        if not vals:
            continue
        headers, rows = parse_rows(vals)
        final_col = format_final_col_name(fmt)
        for row in rows:
            name = (row.get("Name") or row.get("name") or "").strip()
            if not name: continue
            per[name].append({
                "event": event,
                "round": rn,
                "final": final_value(row, final_col)
            })
    persons = [{"name": n, "results": sorted(per[n], key=lambda x: (x["event"], ROUND_ORDER_RANK.get(x["round"], 0)), reverse=True)} for n in per.keys()]
    persons.sort(key=lambda x: x["name"].lower())
    return {"persons": persons}

def build_podiums(comp_name: str, comp_info: dict, sheet_values: dict):
    by_event = {}
    for r in comp_info["rounds"]:
        event = r["event"]; rn = r["round"]; fmt = r["format"]
        if rn.lower().strip() != "final":
            continue
        vals = None
        for cand in tab_candidates(event, rn):
            vals = sheet_values.get(cand)
            if vals: break
        if not vals:
            continue
        headers, rows = parse_rows(vals)
        final_col = format_final_col_name(fmt)
        # top 3 entries
        def parse_num(x):
            try:
                return float(x)
            except:
                return float('inf')
        sorted_rows = sorted(rows, key=lambda row: parse_num(final_value(row, final_col)))
        podium = []
        for idx, row in enumerate(sorted_rows[:3]):
            podium.append({
                "place": idx + 1,
                "name": (row.get("Name") or row.get("name") or "").strip(),
                "final": final_value(row, final_col)
            })
        by_event[event] = podium
    return {"events": by_event}

def build_offered(comp_info: dict):
    # byEvent: event -> [rounds], plus advances map
    by_event = defaultdict(list)
    advances = {}
    for r in comp_info["rounds"]:
        ev = r["event"]; rn = r["round"]
        if rn not in by_event[ev]:
            by_event[ev].append(rn)
        advances[f"{ev} - {rn}"] = r["advance"] if r["advance"] is not None else None
    # order rounds
    for ev in list(by_event.keys()):
        by_event[ev].sort(key=lambda rn: ROUND_ORDER_RANK.get(rn, 0))
    return {"byEvent": by_event, "advance": advances}

def read_details_tab(sheet_id: str):
    """Optional 'Details' tab with key-value pairs (A:B).
       Keys expected: venue, date, time, main_event
    """
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{urllib.parse.quote('Details!A:B')}?key={API_KEY}"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return {}
    vals = r.json().get("values", []) or []
    data = {}
    for row in vals:
        if not row: continue
        key = (row[0] if len(row)>0 else "").strip().lower()
        val = (row[1] if len(row)>1 else "").strip()
        if key:
            data[key] = val
    return data

def main():
    if not API_KEY:
        print("ERROR: GOOGLE_API_KEY/API_KEY not set", file=sys.stderr)
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    comps = read_meta()
    # For each comp, batch-get all offered round tabs (public) from its sheet
    for comp_name, info in comps.items():
        if not info["locked"]:
            continue  # only locked competitions for /competitions
        sheet_id = info["sheet_id"] or MASTER_SHEET_ID

        # collect unique tab ranges
        ranges = []
        seen = set()
        for r in info["rounds"]:
            for cand in tab_candidates(r["event"], r["round"]):
                rr = f"'{cand}'!A:Z"
                if rr not in seen:
                    seen.add(rr)
                    ranges.append(rr)

        sheet_values = fetch_sheet_values(sheet_id, ranges)

        all_json = build_all(comp_name, info, sheet_values)
        persons_json = build_persons(comp_name, info, sheet_values)
        podiums_json = build_podiums(comp_name, info, sheet_values)
        offered_json = build_offered(info)

        # details
        details = read_details_tab(sheet_id)
        # If no date in details, fallback to earliest meta date
        if "date" not in details or not details.get("date"):
            details["date"] = info.get("date","")
        # Build highlights for main_event using podiums
        details_out = {
            "venue": details.get("venue",""),
            "date": details.get("date",""),
            "time": details.get("time",""),
            "main_event": details.get("main_event","") or "",
            "podiums": {}
        }
        if details_out["main_event"]:
            details_out["podiums"][details_out["main_event"]] = podiums_json["events"].get(details_out["main_event"], [])

        # write files
        base = os.path.join(OUT_DIR, slug(comp_name))
        os.makedirs(base, exist_ok=True)
        with open(os.path.join(base, "all.json"), "w", encoding="utf-8") as f:
            json.dump(all_json, f, ensure_ascii=False)
        with open(os.path.join(base, "persons.json"), "w", encoding="utf-8") as f:
            json.dump(persons_json, f, ensure_ascii=False)
        with open(os.path.join(base, "podiums.json"), "w", encoding="utf-8") as f:
            json.dump(podiums_json, f, ensure_ascii=False)
        with open(os.path.join(base, "offered.json"), "w", encoding="utf-8") as f:
            json.dump(offered_json, f, ensure_ascii=False)
        with open(os.path.join(base, "details.json"), "w", encoding="utf-8") as f:
            json.dump(details_out, f, ensure_ascii=False)

        print(f"Prefetched {comp_name} -> {base}", file=sys.stderr)

if __name__ == "__main__":
    main()
