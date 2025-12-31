import os
import json

LSC_DIR = "profiles/lsc"

def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

def main():
    comp_counts = {}
    dnf_counts = {}

    for fn in os.listdir(LSC_DIR):
        if not fn.endswith("-merged.json"):
            continue
        path = os.path.join(LSC_DIR, fn)
        data = load_json(path)
        if not data or "results" not in data:
            continue

        name = data.get("person", {}).get("name", fn.replace("-merged.json", ""))
        results = data["results"]

        comps_2025 = set()
        dnf_2025 = 0

        for r in results:
            comp = r.get("competition_id", "")
            if "2025" not in comp:
                continue

            comps_2025.add(comp)

            attempts = r.get("attempts", [])
            dnf_2025 += sum(1 for a in attempts if a == -1)

        if comps_2025:
            comp_counts[name] = len(comps_2025)
        if dnf_2025 > 0:
            dnf_counts[name] = dnf_2025

    print("\n🏆 Competitions Attended in 2025:")
    for name, count in sorted(comp_counts.items(), key=lambda x: -x[1]):
        print(f"{name}: {count} competitions")

    print("\n❌ DNF Singles in 2025:")
    for name, count in sorted(dnf_counts.items(), key=lambda x: -x[1]):
        print(f"{name}: {count} DNFs")

if __name__ == "__main__":
    main()
