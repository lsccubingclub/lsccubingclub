# config.py

# Google Sheets
import os, json, tempfile
# Use service account file path if provided; otherwise fall back to SERVICE_ACCOUNT_JSON env
_DEFAULT_GC_FILE = "service_account.json"
_sa_env = os.environ.get("SERVICE_ACCOUNT_JSON")
if _sa_env:
    # write a temp file in current repo root (overwrites each run)
    try:
        _tmp_path = os.path.join(os.getcwd(), "service_account_temp.json")
        with open(_tmp_path, "w", encoding="utf-8") as _f:
            # If the env contains a JSON string, write it raw. If it contains escaped content, try to load
            try:
                json_obj = json.loads(_sa_env)
                json.dump(json_obj, _f, ensure_ascii=False, indent=2)
            except Exception:
                _f.write(_sa_env)
        GOOGLE_CREDENTIAL_FILE = _tmp_path
    except Exception:
        GOOGLE_CREDENTIAL_FILE = _DEFAULT_GC_FILE
else:
    GOOGLE_CREDENTIAL_FILE = _DEFAULT_GC_FILE

SHEETS_MASTER_ID = "1fjWyEbc7a5A3RjkFm0BEnE_lyHXCa3kYiKRd2IP9j5Q"

# WCA API
WCA_API_BASE = "https://www.worldcubeassociation.org/api/v0"
USER_AGENT = {"User-Agent": "LSCRecordsBot/1.0 (+https://example.com)"}

API_KEY = "AIzaSyA_4BKwiXfv_T9XhbdenwHuGm0k5uc89S8"

# Caching and output
WCA_CACHE_DIR = "profiles/wca"
LSC_CACHE_DIR = "profiles/lsc"
OUTPUT_DIR = "data"

# Aggregation defaults
START_DATE = "2023-01-01"
RANK_TOP_N = 100

# Event mapping (normalize names to WCA codes used in results)
# Feel free to add/update mappings as needed.
EVENT_NAME_TO_CODE = {
    "3x3x3 Cube": "333", "2x2x2 Cube": "222", "4x4x4 Cube": "444", "5x5x5 Cube": "555",
    "6x6x6 Cube": "666", "7x7x7 Cube": "777", "3x3x3 Blindfolded": "333bf",
    "3x3x3 Fewest Moves": "333fm", "3x3x3 One-handed": "333oh", "Clock": "clock",
    "Megaminx": "minx", "Pyraminx": "pyram", "Skewb": "skewb", "Square-1": "sq1",
    "3x3x3 Multi-Blind": "333mbf", "4x4x4 Blindfolded": "444bf", "5x5x5 Blindfolded": "555bf"
}
