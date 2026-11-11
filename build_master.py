#!/usr/bin/env python3
"""
Master API builder
==================
Source 1: LiveZoneTV321/event/events.json          (events[] flat)
Source 2: sm-monirulislam/Sports_data.json    (matches[] with status)

Output  : master_sports.json  (Sports_data.json format)
"""

import base64
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


# ============================================================
# CONFIG
# ============================================================

API1_URL = (
    "https://raw.githubusercontent.com/"
    "LiveZoneTV321/api1event/refs/heads/main/events.json"
)

API2_URL = (
    "https://raw.githubusercontent.com/"
    "sm-monirulislam/Upcoming-and-Live-Sports-Data/"
    "refs/heads/main/Sports_data.json"
)

MANUAL_CONTROL_FILE = "manual_control.json"

BD_TIMEZONE = ZoneInfo("Asia/Dhaka")

# API1: date + time  ->  startTime
API1_START_FORMAT = "%d/%m/%Y %I:%M %p"
API1_START_OUT_FORMAT = "%d/%m/%Y %I:%M:%S %p"

# API2 startTime format
API2_START_FORMAT = "%d/%m/%Y %I:%M:%S %p"


# ============================================================
# STATUS SETS
# ============================================================

LIVE_SOURCE_STATUSES = {
    "LIVE", "INPLAY", "IN-PLAY", "IN PLAY", "ONGOING",
    "STARTED", "PLAYING", "NOW PLAYING", "RUNNING",
    "1H", "2H", "HT", "ET", "BT", "PEN", "PENALTIES",
    "BREAK", "HALF TIME", "HALFTIME", "FIRST HALF",
    "SECOND HALF", "EXTRA TIME", "PENALTY SHOOTOUT",
    "INT", "SUSP", "SUSPENDED", "PAUSED",
    "Q1", "Q2", "Q3", "Q4", "OT", "OT1", "OT2",
    "1Q", "2Q", "3Q", "4Q",
    "SET1", "SET2", "SET3", "SET4", "SET5",
    "1ST SET", "2ND SET", "3RD SET", "4TH SET", "5TH SET",
    "1S", "2S", "3S", "4S", "5S",
    "INN1", "INN2", "INNINGS", "1ST INN", "2ND INN",
    "INN", "1IN", "2IN",
    "P1", "P2", "P3",
}

ENDED_SOURCE_STATUSES = {
    "ENDED", "END", "FT", "FINISHED", "FULL TIME", "FULLTIME",
    "AET", "AFTER EXTRA TIME", "AP", "AFTER PENALTIES",
    "COMPLETED", "COMPLETE", "ABANDONED", "ABAN",
    "CANCELLED", "CANCELED", "CANC", "POSTPONED", "POSTP",
    "DELAYED", "DEL", "AWD", "AWARDED", "WO", "WALKOVER",
}

UPCOMING_SOURCE_STATUSES = {
    "UPCOMING", "SCHEDULED", "NOT STARTED", "NS",
    "TBD", "PRE", "PRE-GAME", "PRE GAME",
}

# Stream priority (lower = first)
STREAM_PRIORITY_KEYWORDS = [
    "fhd", "full hd", "uhd", "4k",
    "hd", "+ hd", " hd ",
    "sd",
    "low quality", "use vpn",
]


# ============================================================
# HELPERS
# ============================================================

def fetch_json(url):
    req = Request(url, headers={"User-Agent": "master-sports-fetcher/1.0"})
    with urlopen(req, timeout=30) as resp:
        return json.load(resp)


def normalize_text(value):
    if value is None:
        return ""
    value = str(value).strip().lower()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+vs\.?\s+", " vs ", value)
    value = re.sub(r"[^a-z0-9\s]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def generate_event_id(match):
    """Deterministic id from Category + teamA + teamB + eventName + date."""
    if not isinstance(match, dict):
        return "evt_unknown"

    category = normalize_text(match.get("Category", ""))
    event_name = normalize_text(match.get("event_name", ""))
    info = match.get("eventInfo", {}) or {}

    team_a = normalize_text(info.get("teamA", ""))
    team_b = normalize_text(info.get("teamB", ""))
    tournament = normalize_text(info.get("eventName", ""))
    start = normalize_text(info.get("startTime", ""))

    parts = [category, team_a, team_b, tournament]
    identity = "|".join(p for p in parts if p)

    if not identity:
        identity = "|".join(
            p for p in [category, event_name, start] if p
        )

    if not identity:
        identity = "unknown_event"

    h = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"evt_{h}"


def decode_drm_b64(drm_b64):
    """API1: base64(kid:key)  ->  'kid:key'."""
    if not drm_b64:
        return None
    try:
        decoded = base64.b64decode(str(drm_b64)).decode("utf-8").strip()
        if ":" in decoded:
            return decoded
    except Exception:
        pass
    return None


def extract_drm_from_url(stream_url):
    """
    URL থেকে ?|drmScheme=...&drmLicense=KID:KEY অংশটুকু কেটে বের করে আনে।
    """
    if not stream_url:
        return stream_url, None
        
    if "drmLicense=" in stream_url:
        try:
            base_url, params = stream_url.split("drmLicense=", 1)
            if "?" in base_url:
                base_url = base_url.split("?")[0]
            drm_key = params.split("&")[0].strip()
            return base_url, drm_key
        except Exception:
            pass
            
    return stream_url, None


def join_kid_key(kid, key):
    if kid and key:
        return f"{str(kid).strip()}:{str(key).strip()}"
    return None


def parse_start_time(value, fmt):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), fmt).replace(
            tzinfo=BD_TIMEZONE
        )
    except (ValueError, TypeError):
        return None


def parse_event_start_time(match):
    """API-থেকে পাওয়া startTime স্ট্রিং থেকে datetime বের করে"""
    if not isinstance(match, dict):
        return None
    event_info = match.get("eventInfo", {})
    start_time_str = event_info.get("startTime", "")
    if not start_time_str:
        return None
    try:
        return datetime.strptime(start_time_str, API2_START_FORMAT).replace(tzinfo=BD_TIMEZONE)
    except (ValueError, TypeError):
        return None


def stream_priority_rank(name):
    n = str(name or "").lower()
    for i, kw in enumerate(STREAM_PRIORITY_KEYWORDS):
        if kw in n:
            return i
    return len(STREAM_PRIORITY_KEYWORDS)


def get_event_name(match):
    return str(match.get("event_name", "Unknown Event")).strip()


def get_source_status(match):
    return str(match.get("status", match.get("match_status", ""))).strip().upper()


# ============================================================
# API1  ->  MASTER FORMAT
# ============================================================

def adapt_api1_event(event, now):
    if not isinstance(event, dict):
        return None

    league = str(event.get("league", "")).strip()

    if "|" in league:
        category, tournament = league.split("|", 1)
        category = category.strip()
        tournament = tournament.strip()
    else:
        category = league
        tournament = league

    team_a = str(event.get("team1", "")).strip()
    team_b = str(event.get("team2", "")).strip()

    date_str = str(event.get("date", "")).strip()
    time_str = str(event.get("time", "")).strip()

    start_time_out = ""
    start_dt = None
    if date_str and time_str:
        start_dt = parse_start_time(
            f"{date_str} {time_str}", API1_START_FORMAT
        )
        if start_dt:
            start_time_out = start_dt.strftime(API1_START_OUT_FORMAT)
        else:
            start_time_out = f"{date_str} {time_str}"

    # API1-তে স্ট্যাটাস থাকে না, তবে আমরা এখানে ডিফল্ট UPCOMING রাখব
    # আসল স্ট্যাটাস update_match_status এ ঠিক হবে
    status = "UPCOMING"

    event_info = {
        "teamA": team_a,
        "teamB": team_b,
        "teamAFlag": str(event.get("logo1", "")).strip(),
        "teamBFlag": str(event.get("logo2", "")).strip(),
        "eventName": tournament,
        "event_logo": str(event.get("leagueLogo", "")).strip(),
        "startTime": start_time_out,
    }

    streams_out = []
    for s in event.get("streams", []) or []:
        if not isinstance(s, dict):
            continue

        url = str(s.get("url", "")).strip()
        if not url:
            continue

        clean_url, url_drm_key = extract_drm_from_url(url)

        out = {
            "channel_name": str(s.get("name", "")).strip(),
            "stream_url": clean_url,
        }

        ref = str(s.get("referer", "")).strip()
        if ref:
            out["referer"] = ref

        ua = str(s.get("userAgent", "")).strip()
        if ua:
            out["user_agent"] = ua

        drm_key = url_drm_key
        b64_drm = decode_drm_b64(s.get("drm"))
        if b64_drm:
            drm_key = b64_drm

        if drm_key:
            out["drm_key"] = drm_key

        streams_out.append(out)

    match = {
        "status": status,
        "Category": category or "Other",
        "event_name": (
            f"{team_a} Vs {team_b}".strip()
            if (team_a or team_b)
            else tournament or "Unknown Event"
        ),
        "eventInfo": event_info,
        "streams": streams_out,
        "id": generate_event_id({
            "Category": category,
            "event_name": tournament,
            "eventInfo": event_info,
        }),
        "isHot": bool(event.get("isHot", False)),
    }

    return match


# ============================================================
# API2  ->  MASTER FORMAT
# ============================================================

def adapt_api2_match(match):
    if not isinstance(match, dict):
        return None

    out = copy.deepcopy(match)

    out.setdefault("Category", "Other")
    out.setdefault("event_name", "Unknown Event")
    out.setdefault("eventInfo", {})
    out.setdefault("streams", [])
    out.setdefault("status", "UPCOMING")
    out.setdefault("isHot", False)

    for s in out["streams"]:
        if not isinstance(s, dict):
            continue
        if "drm_key" not in s:
            dk = join_kid_key(s.get("kid"), s.get("key"))
            if dk:
                s["drm_key"] = dk
        s.pop("kid", None)
        s.pop("key", None)

    if not str(out.get("id", "")).strip():
        out["id"] = generate_event_id(out)

    return out


# ============================================================
# DEDUP + MERGE
# ============================================================

def dedup_key(match):
    info = match.get("eventInfo", {}) or {}
    category = normalize_text(match.get("Category", ""))
    team_a = normalize_text(info.get("teamA", ""))
    team_b = normalize_text(info.get("teamB", ""))
    start = str(info.get("startTime", "")).strip()
    date_part = start.split(" ")[0] if start else ""
    return f"{category}|{team_a}|{team_b}|{date_part}"


def merge_streams_into(target_streams, new_streams):
    seen = {
        str(s.get("stream_url", "")).strip()
        for s in target_streams
        if isinstance(s, dict)
    }
    added = 0
    for s in new_streams:
        if not isinstance(s, dict):
            continue
        url = str(s.get("stream_url", "")).strip()
        if not url or url in seen:
            continue
        target_streams.append(copy.deepcopy(s))
        seen.add(url)
        added += 1
    return added


def merge_matches(api2_matches, api1_matches):
    master = {}
    order = []

    for m in api2_matches:
        k = dedup_key(m)
        if k in master:
            merge_streams_into(master[k]["streams"], m.get("streams", []))
            master[k]["isHot"] = bool(master[k].get("isHot") or m.get("isHot"))
        else:
            master[k] = m
            order.append(k)

    added_from_api1 = 0
    merged_streams = 0

    for m in api1_matches:
        k = dedup_key(m)
        if k in master:
            merged_streams += merge_streams_into(
                master[k]["streams"], m.get("streams", [])
            )
            master[k]["isHot"] = bool(master[k].get("isHot") or m.get("isHot"))
        else:
            master[k] = m
            order.append(k)
            added_from_api1 += 1

    return [master[k] for k in order], added_from_api1, merged_streams


# ============================================================
# SORTING
# ============================================================

def sort_streams_by_priority(match):
    streams = match.get("streams", [])
    if not isinstance(streams, list):
        return
    streams.sort(
        key=lambda s: stream_priority_rank(s.get("channel_name", ""))
    )


def sort_matches_by_status(matches):
    order = {"LIVE": 0, "UPCOMING": 1, "ENDED": 2}
    matches.sort(
        key=lambda m: order.get(
            str(m.get("status", "")).strip().upper(), 1
        )
    )


# ============================================================
# MANUAL CONTROL
# ============================================================

def load_manual_control():
    try:
        with open(MANUAL_CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        print(f"[MANUAL] {MANUAL_CONTROL_FILE} not found.")
        return {}
    except json.JSONDecodeError as e:
        print(f"[MANUAL] Invalid JSON: {e}")
        return {}


def apply_status_override(match, manual):
    eid = str(match.get("id", "")).strip()
    if not eid:
        return False
    overrides = manual.get("status_override", {})
    if not isinstance(overrides, dict) or eid not in overrides:
        return False
    new_status = str(overrides[eid]).strip().upper()
    if not new_status:
        return False
    match["status"] = new_status
    print(f"[MANUAL STATUS] {eid} -> {new_status}")
    return True


def apply_manual_events_and_streams(matches, manual):
    remove_ids = {
        str(x).strip()
        for x in manual.get("remove_events", []) or []
        if str(x).strip()
    }
    if remove_ids:
        before = len(matches)
        matches = [
            m for m in matches
            if str(m.get("id", "")).strip() not in remove_ids
        ]
        print(f"[MANUAL] Events removed: {before - len(matches)}")

    remove_streams = manual.get("remove_streams", {}) or {}
    if isinstance(remove_streams, dict):
        for m in matches:
            eid = str(m.get("id", "")).strip()
            names = remove_streams.get(eid)
            if not isinstance(names, list):
                continue
            names_set = {str(n).strip().lower() for n in names if str(n).strip()}
            streams = m.get("streams", [])
            m["streams"] = [
                s for s in streams
                if str(s.get("channel_name", "")).strip().lower()
                not in names_set
            ]

    add_streams = manual.get("add_streams", {}) or {}
    if isinstance(add_streams, dict):
        for m in matches:
            eid = str(m.get("id", "")).strip()
            new_list = add_streams.get(eid)
            if not isinstance(new_list, list):
                continue
            existing = m.setdefault("streams", [])
            seen = {
                str(s.get("channel_name", "")).strip().lower()
                for s in existing
            }
            for s in new_list:
                if not isinstance(s, dict):
                    continue
                name = str(s.get("channel_name", "")).strip()
                url = str(s.get("stream_url", "")).strip()
                if not name or not url:
                    continue
                if name.lower() in seen:
                    continue
                existing.append(copy.deepcopy(s))
                seen.add(name.lower())

    add_events = manual.get("add_events", []) or []
    if isinstance(add_events, list):
        existing_ids = {
            str(m.get("id", "")).strip() for m in matches
        }
        for ev in add_events:
            if not isinstance(ev, dict):
                continue
            ev = copy.deepcopy(ev)
            eid = str(ev.get("id", "")).strip() or generate_event_id(ev)
            base = eid
            i = 2
            while eid in existing_ids:
                eid = f"{base}_manual_{i}"
                i += 1
            ev["id"] = eid
            ev.setdefault("streams", [])
            ev.setdefault("isHot", False)
            ev.setdefault("status", "UPCOMING")
            matches.append(ev)
            existing_ids.add(eid)
            print(f"[MANUAL] Event added: {eid}")

    return matches


def apply_hot_events(matches, manual):
    hot_ids = manual.get("hot_events", []) or []
    hot_set = {str(x).strip() for x in hot_ids if str(x).strip()}
    for m in matches:
        eid = str(m.get("id", "")).strip()
        m["isHot"] = eid in hot_set
    return len(hot_set)


# ============================================================
# VALIDATION / CLEANUP
# ============================================================

def remove_empty_stream_urls(matches):
    removed = 0
    for m in matches:
        streams = m.get("streams", [])
        if not isinstance(streams, list):
            continue
        kept = []
        for s in streams:
            if not isinstance(s, dict):
                continue
            
            raw_url = str(s.get("stream_url", s.get("url", ""))).strip()
            url, url_drm = extract_drm_from_url(raw_url)
            
            if not url:
                removed += 1
                continue
                
            s["stream_url"] = url
            if url_drm and "drm_key" not in s:
                s["drm_key"] = url_drm

            kept.append(s)
        m["streams"] = kept
    return removed


# ============================================================
# STATUS UPDATE (CORE LOGIC)
# ============================================================

def update_match_status(match, now_bd, manual_control):
    event_name = get_event_name(match)
    source_status = get_source_status(match)

    # ১. ম্যানুয়াল ওভাররাইড
    if apply_status_override(match, manual_control):
        return match["status"]

    # ২. API ২ থেকে আসা স্ট্যাটাস (LIVE, ENDED, UPCOMING)
    if source_status:
        if source_status in ENDED_SOURCE_STATUSES:
            match["status"] = "ENDED"
            return "ENDED"
        elif source_status in LIVE_SOURCE_STATUSES:
            match["status"] = "LIVE"
            return "LIVE"
        elif source_status in UPCOMING_SOURCE_STATUSES:
            match["status"] = "UPCOMING"
            return "UPCOMING"

    # ৩. API ১-এর ক্ষেত্রে (যেহেতু এখানে status থাকে না)
    start_dt = parse_event_start_time(match)
    if start_dt:
        # ১০ মিনিট আগে থেকে লাইভ ধরার লজিক
        diff_minutes = (now_bd - start_dt).total_seconds() / 60

        if diff_minutes < -10:
            match["status"] = "UPCOMING"
        else:
            match["status"] = "LIVE"  
    else:
        match["status"] = "UPCOMING"

    return match["status"]


def update_all_match_statuses(matches, now_bd, manual_control):
    live_count = 0
    upcoming_count = 0
    ended_count = 0

    for match in matches:
        status = update_match_status(match, now_bd, manual_control)
        if status == "LIVE":
            live_count += 1
        elif status == "UPCOMING":
            upcoming_count += 1
        elif status == "ENDED":
            ended_count += 1

    return live_count, upcoming_count, ended_count


# ============================================================
# MAIN
# ============================================================

def main():
    output_file = sys.argv[1] if len(sys.argv) > 1 else "master_sports.json"
    now = datetime.now(BD_TIMEZONE)

    print("=" * 70)
    print("MASTER SPORTS BUILDER")
    print("BD Now:", now.strftime(API2_START_FORMAT))
    print("=" * 70)

    try:
        # 1. Fetch
        print("[FETCH] API1 ...")
        api1_data = fetch_json(API1_URL)
        api1_raw = api1_data.get("events", []) if isinstance(api1_data, dict) else []
        print(f"  API1 events: {len(api1_raw)}")

        print("[FETCH] API2 ...")
        api2_data = fetch_json(API2_URL)
        api2_raw = api2_data.get("matches", []) if isinstance(api2_data, dict) else []
        print(f"  API2 matches: {len(api2_raw)}")

        # 2. Adapt
        api1_matches = []
        for ev in api1_raw:
            m = adapt_api1_event(ev, now)
            if m:
                api1_matches.append(m)
        print(f"[ADAPT] API1 -> master: {len(api1_matches)}")

        api2_matches = []
        for ev in api2_raw:
            m = adapt_api2_match(ev)
            if m:
                api2_matches.append(m)
        print(f"[ADAPT] API2 -> master: {len(api2_matches)}")

        # 3. Merge (API2 primary)
        merged, added1, merged_streams = merge_matches(
            api2_matches, api1_matches
        )
        print(f"[MERGE] Total: {len(merged)} "
              f"(API2 {len(api2_matches)} + new API1 {added1})")
        print(f"[MERGE] Extra streams merged from API1: {merged_streams}")

        # 4. Manual control
        manual = load_manual_control()
        merged = apply_manual_events_and_streams(merged, manual)

        # 5. Remove empty URLs
        removed_empty = remove_empty_stream_urls(merged)
        print(f"[CLEAN] Empty streams removed: {removed_empty}")

        # 6. Update Statuses (এখানে লজিক কাজ করবে)
        live, upcoming, ended = update_all_match_statuses(merged, now, manual)
        
        # 7. Sort
        for m in merged:
            sort_streams_by_priority(m)
        sort_matches_by_status(merged)

        # 8. Hot
        hot_count = apply_hot_events(merged, manual)
        print(f"[HOT] Hot events: {hot_count}")

        # 9. Output
        out = {
            "name": "Live and Upcoming Sports Data",
            "telegram_channel": "https://t.me/LiveZoneTV",
            "last_update_time": now.strftime("%I:%M:%S %p %d-%m-%Y"),
            "total_matches": len(merged),
            "live_match": live,
            "upcoming_match": upcoming,
            "ended_match": ended,
            "sources": ["api1:events.json", "api2:Sports_data.json"],
            "matches": merged,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.write("\n")

        print("=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Total   : {len(merged)}")
        print(f"LIVE    : {live}")
        print(f"UPCOMING: {upcoming}")
        print(f"ENDED   : {ended}")
        print(f"Output  : {output_file}")
        print("=" * 70)

        return 0

    except (HTTPError, URLError, TimeoutError,
            json.JSONDecodeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
