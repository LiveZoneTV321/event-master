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
import unicodedata
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
# TEAM NAME NORMALIZATION (fix duplicate)
# ============================================================

# একই টিমের ভিন্ন বানান একসাথে আনতে alias map
TEAM_ALIASES = {
    "turkey": "turkiye",
    "ivory coast": "cote divoire",
    "uae": "united arab emirates",
    "usa": "united states",
    "czechia": "czech republic",
    "south korea": "korea republic",
    "north korea": "korea dpr",
    "bosnia herzegovina": "bosnia and herzegovina",
    "hb koge": "koge",
    "hb køge": "koge",
    "koge w": "koge",
    "køge w": "koge",
    "oudheverlee leuven w": "oudheverlee leuven",
    "st johns red storm": "st johns red storm",
}

# corrupt / placeholder টিম — এগুলো থাকলে ম্যাচ বাদ যাবে
BLACKLIST_TEAMS = {
    "motherfucker", "unknown", "tbd", "tbc", "team 1", "team 2",
}

WOMEN_SUFFIX_RE = re.compile(
    r"\(w\)|\(women\)|\(women's\)|\bw\b|\bwomen\b|\bwomen's\b|\bladies\b|\bfeminine\b",
    re.IGNORECASE,
)


def normalize_team_name(name):
    """টিমের নাম normalize — duplicate ধরার জন্য।
    - lowercase
    - unicode decompose (ø→o, ü→u)
    - (W), Women সাফিক্স সরানো
    - punctuation বাদ
    - alias apply
    """
    if name is None:
        return ""
    s = str(name).strip().lower()
    # unicode → ascii
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Women সাফিক্স সরাও
    s = WOMEN_SUFFIX_RE.sub(" ", s)
    # punctuation → space
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # alias
    return TEAM_ALIASES.get(s, s)


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
    if not isinstance(match, dict):
        return "evt_unknown"
    category = normalize_text(match.get("Category", ""))
    event_name = normalize_text(match.get("event_name", ""))
    info = match.get("eventInfo", {}) or {}
    team_a = normalize_team_name(info.get("teamA", ""))
    team_b = normalize_team_name(info.get("teamB", ""))
    tournament = normalize_text(info.get("eventName", ""))
    start = normalize_text(info.get("startTime", ""))
    parts = [category, team_a, team_b, tournament]
    identity = "|".join(p for p in parts if p)
    if not identity:
        identity = "|".join(p for p in [category, event_name, start] if p)
    if not identity:
        identity = "unknown_event"
    h = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"evt_{h}"


def decode_drm_b64(drm_b64):
    if not drm_b64:
        return None

    raw = str(drm_b64).strip()

    # ✅ plain KID:KEY হলে সরাসরি ফেরত (32 hex : 32 hex)
    if re.match(r"^[0-9a-fA-F]{32}:[0-9a-fA-F]{32}$", raw):
        return raw.lower()

    # পুরনো base64 লজিক — অপরিবর্তিত
    try:
        decoded = base64.b64decode(raw).decode("utf-8").strip()
        if ":" in decoded:
            return decoded
    except Exception:
        pass
    return None


def extract_drm_from_url(stream_url):
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
# DEDUP + MERGE  (fix duplicate)
# ============================================================

def _match_identity(match):
    """match থেকে normalized identity বের করে (team, eventName, date)।"""
    info = match.get("eventInfo", {}) or {}
    category = normalize_text(match.get("Category", ""))
    team_a = normalize_team_name(info.get("teamA", ""))
    team_b = normalize_team_name(info.get("teamB", ""))
    event_name = normalize_text(
        info.get("eventName", "") or match.get("event_name", "")
    )
    start = str(info.get("startTime", "")).strip()
    date_part = start.split(" ")[0] if start else ""
    return {
        "category": category,
        "team_a": team_a,
        "team_b": team_b,
        "event_name": event_name,
        "date": date_part,
    }


def is_valid_match(match):
    """corrupt / placeholder টিম বাদ দেয়।"""
    ident = _match_identity(match)
    for t in (ident["team_a"], ident["team_b"]):
        if t in BLACKLIST_TEAMS:
            return False
    return True


def _is_same_match(a_ident, b_ident):
    """দুই match একই কিনা (duplicate detection rules)।"""
    if a_ident["category"] != b_ident["category"]:
        return False

    a_ta, a_tb = a_ident["team_a"], a_ident["team_b"]
    b_ta, b_tb = b_ident["team_a"], b_ident["team_b"]

    # নিয়ম ১: teamA ও teamB দুটোই মিলছে → merge
    if a_ta and a_tb and a_ta == b_ta and a_tb == b_tb:
        return True

    # নিয়ম ১বি: টিমের ক্রম উল্টে গেলেও merge
    if a_ta and a_tb and a_ta == b_tb and a_tb == b_ta:
        return True

    # নিয়ম ২: eventName মিলছে + date মিলছে + অন্তত একটা team মিলছে → merge
    if (
        a_ident["event_name"]
        and a_ident["event_name"] == b_ident["event_name"]
        and a_ident["date"]
        and a_ident["date"] == b_ident["date"]
    ):
        teams_overlap = (
            (a_ta and (a_ta == b_ta or a_ta == b_tb))
            or (a_tb and (a_tb == b_ta or a_tb == b_tb))
        )
        if teams_overlap:
            return True

    return False


def _find_existing_match(master_list, new_match):
    """master list-এ same match খুঁজে ফেরত দেয়; না পেলে None।"""
    new_ident = _match_identity(new_match)
    for existing in master_list:
        if _is_same_match(_match_identity(existing), new_ident):
            return existing
    return None


def merge_streams_into(target_streams, new_streams):
    """stream merge — URL মিললে metadata enrich, নতুন হলে append।"""
    by_url = {
        str(s.get("stream_url", "")).strip(): s
        for s in target_streams if isinstance(s, dict)
    }
    added = 0
    for s in new_streams:
        if not isinstance(s, dict):
            continue
        url = str(s.get("stream_url", "")).strip()
        if not url:
            continue
        if url in by_url:
            existing = by_url[url]
            for k in ("drm_key", "referer", "user_agent"):
                if not existing.get(k) and s.get(k):
                    existing[k] = s[k]
        else:
            target_streams.append(copy.deepcopy(s))
            by_url[url] = target_streams[-1]
            added += 1
    return added


def merge_matches(api2_matches, api1_matches):
    """API2 primary, API1 থেকে stream ও নতুন match merge।"""
    # corrupt data বাদ
    api2_matches = [m for m in api2_matches if is_valid_match(m)]
    api1_matches = [m for m in api1_matches if is_valid_match(m)]

    master = []

    # ১) API2 সব ঢোকাই
    for m in api2_matches:
        existing = _find_existing_match(master, m)
        if existing is not None:
            merge_streams_into(existing["streams"], m.get("streams", []))
            existing["isHot"] = bool(existing.get("isHot") or m.get("isHot"))
        else:
            master.append(m)

    # ২) API1 merge / add
    added_from_api1 = 0
    merged_streams = 0
    for m in api1_matches:
        existing = _find_existing_match(master, m)
        if existing is not None:
            merged_streams += merge_streams_into(existing["streams"], m.get("streams", []))
            existing["isHot"] = bool(existing.get("isHot") or m.get("isHot"))
        else:
            master.append(m)
            added_from_api1 += 1

    return master, added_from_api1, merged_streams


# ============================================================
# SORTING
# ============================================================

def sort_streams_by_priority(match):
    streams = match.get("streams", [])
    if not isinstance(streams, list):
        return
    streams.sort(key=lambda s: stream_priority_rank(s.get("channel_name", "")))


def sort_matches_by_status(matches):
    order = {"LIVE": 0, "UPCOMING": 1, "ENDED": 2}
    matches.sort(key=lambda m: order.get(str(m.get("status", "")).strip().upper(), 1))


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
    # ✅ নতুন: merge_events — ২টা ID-র ম্যাচ merge করে এক করো
    merge_pairs = manual.get("merge_events", []) or []
    if isinstance(merge_pairs, list) and merge_pairs:
        by_id = {str(m.get("id", "")).strip(): m for m in matches}
        drop_ids = set()
        for pair in merge_pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            keep_id = str(pair[0]).strip()
            drop_id = str(pair[1]).strip()
            if keep_id == drop_id:
                continue
            keep_m = by_id.get(keep_id)
            drop_m = by_id.get(drop_id)
            if not keep_m or not drop_m:
                print(f"[MANUAL MERGE] skipped: {pair} (one id missing)")
                continue
            added = merge_streams_into(
                keep_m.setdefault("streams", []),
                drop_m.get("streams", [])
            )
            keep_m["isHot"] = bool(keep_m.get("isHot") or drop_m.get("isHot"))
            drop_ids.add(drop_id)
            print(f"[MANUAL MERGE] {drop_id} -> {keep_id} (+{added} streams)")
        if drop_ids:
            matches = [m for m in matches if str(m.get("id", "")).strip() not in drop_ids]
            print(f"[MANUAL MERGE] Removed duplicate events: {len(drop_ids)}")

    remove_ids = {str(x).strip() for x in manual.get("remove_events", []) or [] if str(x).strip()}
    if remove_ids:
        before = len(matches)
        matches = [m for m in matches if str(m.get("id", "")).strip() not in remove_ids]
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
            m["streams"] = [s for s in streams if str(s.get("channel_name", "")).strip().lower() not in names_set]

    add_streams = manual.get("add_streams", {}) or {}
    if isinstance(add_streams, dict):
        for m in matches:
            eid = str(m.get("id", "")).strip()
            new_list = add_streams.get(eid)
            if not isinstance(new_list, list):
                continue
            existing = m.setdefault("streams", [])
            seen = {str(s.get("channel_name", "")).strip().lower() for s in existing}
            for s in new_list:
                if not isinstance(s, dict):
                    continue
                name = str(s.get("channel_name", "")).strip()
                url = str(s.get("stream_url", "")).strip()
                if not name or not url or name.lower() in seen:
                    continue
                existing.append(copy.deepcopy(s))
                seen.add(name.lower())

    add_events = manual.get("add_events", []) or []
    if isinstance(add_events, list):
        existing_ids = {str(m.get("id", "")).strip() for m in matches}
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
    source_status = get_source_status(match)

    # ১. ম্যানুয়াল ওভাররাইড (সবচেয়ে বেশি প্রায়োরিটি)
    if apply_status_override(match, manual_control):
        return match["status"]

    has_streams = bool(match.get("streams", []))
    start_dt = parse_event_start_time(match)

    # ২. API ২-এর স্ট্যাটাস (সোর্স অফ ট্রুথ)
    if source_status:
        if source_status in ENDED_SOURCE_STATUSES:
            match["status"] = "ENDED"
            return "ENDED"
        elif source_status in LIVE_SOURCE_STATUSES:
            match["status"] = "LIVE"
            return "LIVE"
        elif source_status in UPCOMING_SOURCE_STATUSES:
            if has_streams and start_dt:
                diff_minutes = (now_bd - start_dt).total_seconds() / 60
                if diff_minutes >= -10:
                    match["status"] = "LIVE"
                    return "LIVE"
            match["status"] = "UPCOMING"
            return "UPCOMING"

    # ৩. API ১-এর জন্য লজিক (স্ট্রিম থাকলে)
    if has_streams and start_dt:
        diff_minutes = (now_bd - start_dt).total_seconds() / 60
        if diff_minutes >= -10:
            if diff_minutes > 600:
                match["status"] = "ENDED"
                return "ENDED"
            else:
                match["status"] = "LIVE"
                return "LIVE"

    # ৪. ফলব্যাক
    if start_dt:
        diff_minutes = (now_bd - start_dt).total_seconds() / 60
        if -10 <= diff_minutes <= 600:
            match["status"] = "LIVE"
            return "LIVE"
        elif diff_minutes > 600:
            match["status"] = "ENDED"
            return "ENDED"
        else:
            match["status"] = "UPCOMING"
            return "UPCOMING"

    match["status"] = "UPCOMING"
    return "UPCOMING"


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

        # 3. Merge (API2 primary, streams merged from API1)
        merged, added1, merged_streams = merge_matches(api2_matches, api1_matches)
        print(f"[MERGE] Total: {len(merged)} (API2 {len(api2_matches)} + new API1 {added1})")
        print(f"[MERGE] Extra streams merged from API1: {merged_streams}")

        # 4. Manual control
        manual = load_manual_control()
        merged = apply_manual_events_and_streams(merged, manual)

        # 5. Remove empty URLs
        removed_empty = remove_empty_stream_urls(merged)
        print(f"[CLEAN] Empty streams removed: {removed_empty}")

        # 6. Update Statuses
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

    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
