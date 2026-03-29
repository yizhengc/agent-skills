#!/usr/bin/env python3
"""
Email triage agent — 2-stage architecture.

Stage 1 (Haiku): cheap classifier — decides if email is worth processing.
Stage 2 (Sonnet): type-aware extractor — pulls events, actions from full body.

Python code is fully generic; Claude handles all type-specific logic.

Usage:
  # Normal scheduled run (last 4h, up to 20 emails)
  python email_agent.py

  # Test with school emails only
  python email_agent.py --query "from:nuevaschool.org OR from:instructure.com" --max 3 --dry-run

  # Test on specific email types
  python email_agent.py --query "subject:portland" --max 3 --dry-run

Config: ~/.config/gsuite-skill/agent_config.json
  - imessage_phone: your phone number for iMessage notifications
  - anthropic_api_key: your Anthropic API key
  - ninth_grade_start_year: the calendar year your kids started 9th grade (e.g. 2025)
  - extracurriculars: list of activities (e.g. ["varsity tennis"])
"""
import argparse
import html as html_module
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

CONFIG_FILE = os.path.expanduser("~/.config/gsuite-skill/agent_config.json")
SEEN_FILE = os.path.expanduser("~/.config/gsuite-skill/seen_emails.json")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.expanduser("~/.config/gsuite-skill/agent.log")
SEEN_RETENTION_DAYS = 14


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(f"ERROR: Config not found at {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


def _load_timezone() -> ZoneInfo:
    """Load timezone from config at startup; fall back to UTC if missing."""
    try:
        with open(CONFIG_FILE) as f:
            return ZoneInfo(json.load(f).get("timezone", "America/Los_Angeles"))
    except Exception:
        return ZoneInfo("America/Los_Angeles")


LA_TZ = _load_timezone()


def current_grade_label(ninth_grade_start_year: int) -> str:
    """Return the current grade label, calculated dynamically each run."""
    now = datetime.now(LA_TZ)
    school_year_start = now.year if now.month >= 8 else now.year - 1
    grade_num = school_year_start - ninth_grade_start_year + 9
    names = {9: "9th grade (Freshman)", 10: "10th grade (Sophomore)",
             11: "11th grade (Junior)", 12: "12th grade (Senior)"}
    return names.get(grade_num, f"{grade_num}th grade")


# ── Seen-email deduplication ──────────────────────────────────────────────────

def load_seen() -> dict:
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE) as f:
        data = json.load(f)
    cutoff = datetime.now(LA_TZ).timestamp() - SEEN_RETENTION_DAYS * 86400
    return {k: v for k, v in data.items() if v >= cutoff}


def save_seen(seen: dict):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f)


def mark_seen(seen: dict, email_id: str):
    seen[email_id] = datetime.now(LA_TZ).timestamp()


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now(LA_TZ).strftime("%Y-%m-%d %H:%M %Z")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── URL content fetching ──────────────────────────────────────────────────────

# URL patterns to skip (unsubscribe, tracking, social, maps)
_SKIP_URL_PATTERNS = re.compile(
    r'unsubscribe|track|facebook|twitter|instagram|linkedin|youtube|google\.com/maps',
    re.IGNORECASE
)


def extract_primary_url(body: str) -> Optional[str]:
    """Return the first non-tracking URL found in an email body, or None."""
    for url in re.findall(r'https?://[^\s\)\"\'\]]+', body):
        if not _SKIP_URL_PATTERNS.search(url):
            return url.rstrip('.,;')
    return None


def fetch_url_text(url: str, max_chars: int = 4000) -> Optional[str]:
    """Fetch a URL and return clean plain text (HTML stripped, entities decoded)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        text = re.sub(r'<[^>]+>', ' ', raw)
        text = html_module.unescape(text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception as e:
        log(f"  WARNING: Could not fetch URL content: {e}")
        return None


# ── Email fetching ─────────────────────────────────────────────────────────────

def fetch_email_list(hours: int, max_emails: int, query: str = "") -> List[Dict]:
    q = query if query else f"in:inbox newer_than:{hours}h"
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "read_email.py"),
         "--query", q, "--max", str(max_emails)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"ERROR fetching email list: {result.stdout.strip() or result.stderr.strip()}")
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        log(f"ERROR parsing email list JSON: {result.stdout[:300]}")
        return []


def fetch_full_email(msg_id: str) -> Optional[Dict]:
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "read_email.py"), "--id", msg_id],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"  ERROR fetching full email {msg_id}: {result.stdout.strip() or result.stderr.strip()}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        log(f"  ERROR parsing full email JSON for {msg_id}")
        return None


# ── Stage 1: cheap classifier (Haiku) ─────────────────────────────────────────

CLASSIFY_PROMPT = """\
You are a quick email classifier for a parent of high school students.

Context:
- Kids are in {grade_label} at {school_name} ({school_location})
- Kids' names: {kids}
- Extracurriculars: {extracurriculars}
- Kids' team level: {team_level} (e.g. VA = Varsity)
- Today: {now}

Classify this email using only the headers and snippet below.
Note: some school emails (e.g. from myschoolapp.com, myschoolemails.com, ParentSquare) may have
an empty From field — rely on the subject and snippet to classify those.

From: {sender}
Subject: {subject}
Snippet: {snippet}

Output a single JSON object (no markdown):
- "email_type": one of:
    "sports_schedule"     — any email from a coach or sports program, including weekly recaps/updates
                            (these often contain upcoming schedules — ALWAYS use this type for coach emails,
                            never classify them as "school_announcement")
    "school_event"        — specific event, deadline, or meeting from school/canvas
    "school_announcement" — general school newsletter or informational update (NOT from a coach)
    "action_required"     — something the parent must do (sign, pay, RSVP, etc.)
    "financial"           — bill, invoice, bank alert, payment, tax
    "health"              — medical, insurance, pharmacy, lab
    "personal"            — email from a person (classmate parent, friend, etc.)
    "ignore"              — marketing, LinkedIn, job alerts, ads, automated noise, \
                            OR Google Calendar invitation/notification emails (subjects starting with \
                            "Invitation:", "Updated invitation:", "Accepted:", "Declined:", "Canceled:", \
                            "Forwarded invitation:") — these are already handled by Google Calendar directly
- "priority": "HIGH" | "MEDIUM" | "LOW" | "IGNORE"
- "grade_relevant": true if relevant to this family's kids, false only if explicitly for a different grade
- "reason": one sentence explaining your decision
"""


def _parse_json_response(text: str) -> Optional[Dict]:
    """Robustly extract JSON from a Claude response that may contain markdown fences."""
    if "```" in text:
        for part in text.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                text = part
                break
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def classify_email(client, summary: Dict, now_str: str, grade_label: str,
                   extracurriculars: List[str], school_name: str,
                   school_location: str, kids: List[str],
                   team_level: str) -> Optional[Dict]:
    """Stage 1: cheap Haiku classifier. Returns {email_type, priority, grade_relevant, reason}."""
    extras = ", ".join(extracurriculars) if extracurriculars else "none listed"
    prompt = CLASSIFY_PROMPT.format(
        grade_label=grade_label,
        school_name=school_name,
        school_location=school_location,
        kids=", ".join(kids) if kids else "not specified",
        extracurriculars=extras,
        team_level=team_level or "not specified",
        now=now_str,
        sender=summary.get("from", "unknown"),
        subject=summary.get("subject", "(no subject)"),
        snippet=(summary.get("snippet") or "")[:400],
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json_response(msg.content[0].text.strip())
        if result is None:
            log(f"  ERROR parsing classify JSON: {msg.content[0].text[:100]}")
        return result
    except Exception as e:
        log(f"  ERROR in classify_email: {e}")
        return None


# ── Stage 2: type-aware extractor (Sonnet) ────────────────────────────────────

EXTRACT_PROMPT = """\
You are a personal assistant extracting structured information from an email.

Context about the family:
- Kids are in {grade_label} at {school_name} ({school_location})
- Kids' names: {kids}
- Extracurriculars: {extracurriculars}
- Kids' team level: {team_level} — only extract events for this level; skip events for other levels (e.g. if team_level is "VA", ignore all JV events)
- Email type: {email_type}
- Today: {now}

Read the full email carefully. Extract all actionable information.

Guidelines by email type:
- "sports_schedule": extract EVERY game/match/practice as a separate event with exact date/time. \
  Do not merge multiple events. Recurring weekly updates still contain distinct scheduled events. \
  If no start time is specified for a game/practice/match, assume 3:00 PM (school ends at 3pm, \
  activities start right after). Include "3:00 PM" in datetime_str in that case.
- "school_event": extract all specific events, deadlines, sign-ups.
- "school_announcement": this is a newsletter that often contains MULTIPLE embedded events and deadlines.
  Scan the entire body carefully. Extract EVERY item that has a specific date or deadline as a separate
  event. Extract items requiring parent/student action (RSVP, sign up, course selection, donate by X)
  as actions. Do NOT return FYI if there are any dated events or action items — use MEETING_EVENT or ACTION.
- "action_required": identify what needs to be done, by whom, and by when.
- "financial" / "health": note any required actions or important dates.
- "personal": extract any proposed meeting/event or action item.

Output a single JSON object (no markdown):
- "category": "MEETING_EVENT" | "ACTION" | "FYI" | "IGNORE"
- "events": for MEETING_EVENT, an array of ALL distinct events, each with:
    - "title": concise title ≤60 chars, e.g. "Varsity Tennis vs Menlo (Away)"
    - "datetime_str": date and time as written in email, e.g. "Mon March 23 at 3:30 PM" (null if unknown)
    - "location": venue name and address if mentioned, else null
    - "duration_minutes": estimated duration in minutes (default 60 for games, 30 for practices)
  If not MEETING_EVENT, set to [].
- "action_description": what the parent needs to do or know (2–3 sentences), or null
- "is_recurring": true if this is a regularly scheduled digest/newsletter, false otherwise
- "summary": 1–2 sentence summary of the email's key content

Email:
From: {sender}
Date: {date}
Subject: {subject}
Body:
{body}
"""


def extract_email(client, full_email: Dict, email_type: str, now_str: str,
                  grade_label: str, extracurriculars: List[str],
                  school_name: str, school_location: str,
                  kids: List[str], team_level: str) -> Optional[Dict]:
    """Stage 2: Sonnet extractor. Returns {category, events, action_description, is_recurring, summary}."""
    extras = ", ".join(extracurriculars) if extracurriculars else "none listed"
    prompt = EXTRACT_PROMPT.format(
        grade_label=grade_label,
        school_name=school_name,
        school_location=school_location,
        kids=", ".join(kids) if kids else "not specified",
        extracurriculars=extras,
        team_level=team_level or "not specified",
        email_type=email_type,
        now=now_str,
        sender=full_email.get("from", "unknown"),
        date=full_email.get("date", "unknown"),
        subject=full_email.get("subject", "(no subject)"),
        body=(full_email.get("body") or full_email.get("snippet") or "")[:4000],
    )
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json_response(msg.content[0].text.strip())
        if result is None:
            log(f"  ERROR parsing extract JSON: {msg.content[0].text[:100]}")
        return result
    except Exception as e:
        log(f"  ERROR in extract_email: {e}")
        return None


# ── Calendar helpers ───────────────────────────────────────────────────────────

def parse_datetime_iso(client, datetime_str: str, now_str: str,
                        default_time: str = "15:00") -> Optional[str]:
    """Convert natural language datetime to ISO 8601 using Claude Haiku.

    default_time: HH:MM to use when no time is present in datetime_str (24h, e.g. "15:00").
    """
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content": (
                f"Today is {now_str} ({LA_TZ}). "
                f"Convert to ISO 8601 with timezone offset: '{datetime_str}'. "
                f"If no time is specified, use {default_time} (local time). "
                "Output ONLY the datetime, e.g.: 2026-03-24T16:00:00-07:00"
            )}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        log(f"  ERROR parsing datetime '{datetime_str}': {e}")
        return None


def load_existing_events(days: int = 60) -> List[Dict]:
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "read_calendar.py"), "--days", str(days)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return []


def _title_keywords(title: str) -> set:
    stopwords = {"vs", "at", "the", "a", "an", "and", "or", "home", "away", "nueva", "jv", "va"}
    return {w.lower() for w in title.split() if len(w) > 2 and w.lower() not in stopwords}


def find_duplicate(title: str, iso_dt: str, existing_events: List[Dict]) -> Optional[Dict]:
    """Return the matching existing event if a similar one exists on the same date, else None."""
    date_part = iso_dt[:10]
    new_words = _title_keywords(title)
    if not new_words:
        return None
    for event in existing_events:
        existing_start = event.get("start", "")
        if date_part not in existing_start:
            continue
        existing_words = _title_keywords(event.get("summary", ""))
        if not existing_words:
            continue
        overlap = new_words & existing_words
        if len(overlap) / len(new_words) >= 0.5:
            return event
    return None


def _is_explicit_time(iso_dt: str) -> bool:
    """Return True if the datetime has a specific non-midnight time."""
    if "T" not in iso_dt:
        return False
    time_part = iso_dt.split("T")[1][:8]  # HH:MM:SS
    return time_part != "00:00:00"


def _existing_is_vague(event: Dict) -> bool:
    """Return True if the existing event has no specific time (all-day or midnight)."""
    start = event.get("start", "")
    # All-day events from Calendar API use 'date' key with no time; check that too
    if "T" not in start:
        return True
    return "00:00:00" in start


def delete_calendar_event(event_id: str, calendar_id: str, dry_run: bool) -> bool:
    if dry_run:
        log(f"  [DRY RUN] Delete event: {event_id}")
        return True
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "write_calendar.py"),
         "--delete-id", event_id, "--calendar-id", calendar_id],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        log(f"  ERROR deleting event {event_id}: {result.stdout.strip()}")
        return False
    log(f"  Deleted stale event: {event_id}")
    return True


def create_calendar_event(client, title: str, datetime_str: str, description: str,
                           existing_events: List[Dict], now_str: str,
                           dry_run: bool, calendar_id: str = "primary",
                           duration_minutes: int = 60,
                           default_time: str = "09:00",
                           attendees: List[str] = None) -> bool:
    iso_dt = parse_datetime_iso(client, datetime_str, now_str, default_time=default_time)
    if not iso_dt or not iso_dt[:4].isdigit():
        log(f"  Skipping — could not parse datetime: {title} ({datetime_str})")
        return False

    dup = find_duplicate(title, iso_dt, existing_events)
    if dup:
        new_explicit = _is_explicit_time(iso_dt)
        existing_vague = _existing_is_vague(dup)

        if new_explicit and existing_vague:
            # Update: delete old vague event, then create the explicit one
            log(f"  Replacing vague event '{dup.get('summary')}' with explicit: {title} @ {iso_dt}")
            deleted = delete_calendar_event(dup.get("id", ""), calendar_id, dry_run)
            if not deleted:
                return False
            # Remove from local list so we don't check against it again
            existing_events[:] = [e for e in existing_events if e.get("id") != dup.get("id")]
        else:
            log(f"  Skipping duplicate: {title} ({iso_dt[:10]})")
            return False

    if dry_run:
        log(f"  [DRY RUN] Calendar event: {title} @ {iso_dt}")
        return True

    cmd = [sys.executable, os.path.join(SCRIPTS_DIR, "write_calendar.py"),
           "--title", title, "--iso-datetime", iso_dt,
           "--description", description,
           "--duration", str(duration_minutes),
           "--calendar-id", calendar_id]
    if attendees:
        cmd += ["--attendees", ",".join(attendees)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stdout = result.stdout.strip()
        real_errors = [l for l in stdout.splitlines() if l.startswith("ERROR")]
        log(f"  ERROR creating calendar event: {'; '.join(real_errors) or stdout[:200]}")
        return False
    log(f"  Created: {title} @ {iso_dt}")
    return True


# ── macOS actions ─────────────────────────────────────────────────────────────

def add_reminder(title: str, notes: str, dry_run: bool) -> bool:
    if dry_run:
        log(f"  [DRY RUN] Reminder: {title}")
        return True
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_notes = notes.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''tell application "Reminders"
    set newReminder to make new reminder at end of default list
    set name of newReminder to "{safe_title}"
    set body of newReminder to "{safe_notes}"
end tell'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"  ERROR adding reminder: {result.stderr.strip()}")
        return False
    return True


def send_imessage(phone: str, message: str, dry_run: bool) -> bool:
    if dry_run:
        log(f"  [DRY RUN] iMessage to {phone}:\n{message}")
        return True
    if phone.isdigit() and len(phone) == 10:
        phone = "+1" + phone
    safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''tell application "Messages"
    set targetService to first service whose service type = iMessage
    set targetBuddy to buddy "{phone}" of targetService
    send "{safe_msg}" to targetBuddy
end tell'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        log(f"  ERROR sending iMessage: {result.stderr.strip()}")
        return False
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Email triage agent")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    parser.add_argument("--hours", type=int, default=4, help="Look back N hours (default: 4)")
    parser.add_argument("--max", type=int, default=20, help="Max emails to process (default: 20)")
    parser.add_argument("--query", default="", help="Custom Gmail search query (overrides --hours)")
    args = parser.parse_args()

    config = load_config()
    phone = config.get("imessage_phone", "")
    if not phone or phone == "YOUR_PHONE_NUMBER_HERE":
        log("ERROR: imessage_phone not set in agent_config.json")
        sys.exit(1)

    api_key = config.get("anthropic_api_key", "")
    if api_key and api_key != "YOUR_ANTHROPIC_API_KEY_HERE":
        os.environ["ANTHROPIC_API_KEY"] = api_key
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        log("ERROR: anthropic_api_key not set in agent_config.json or environment")
        sys.exit(1)

    ninth_grade_start_year = config.get("ninth_grade_start_year", 2025)
    grade_label = current_grade_label(ninth_grade_start_year)
    extracurriculars = config.get("extracurriculars", [])
    calendar_attendees = config.get("calendar_attendees", [])
    school_name = config.get("school_name", "their school")
    school_location = config.get("school_location", "")
    kids = config.get("kids", [])
    team_level = config.get("team_level", "")

    try:
        import anthropic
    except ImportError:
        log("ERROR: pip install anthropic")
        sys.exit(1)

    label = f"query='{args.query}'" if args.query else f"last {args.hours}h"
    log(f"Starting email triage ({label}, max={args.max}, grade={grade_label}, dry_run={args.dry_run})")

    email_list = fetch_email_list(hours=args.hours, max_emails=args.max, query=args.query)
    if not email_list:
        log("No emails found.")
        return

    seen = load_seen()
    new_emails = [e for e in email_list if e["id"] not in seen]
    skipped = len(email_list) - len(new_emails)
    if skipped:
        log(f"  Skipping {skipped} already-processed email(s).")
    if not new_emails:
        log("No new unprocessed emails.")
        save_seen(seen)
        return

    log(f"Processing {len(new_emails)} new email(s)...")
    client = anthropic.Anthropic()
    now_str = datetime.now(LA_TZ).strftime("%A, %B %d, %Y %I:%M %p %Z")

    existing_events = load_existing_events(days=60)

    notifications = []
    calendar_created = []
    reminders_created = []

    for summary in new_emails:
        subject = summary.get("subject", "(no subject)")
        sender = summary.get("from", "unknown")
        log(f"  [{sender[:30]}] {subject[:55]}")

        # Pre-filter: skip Google Calendar invite/notification emails
        subject = summary.get("subject", "")
        gcal_prefixes = ("Invitation:", "Updated invitation:", "Accepted:", "Declined:",
                         "Canceled:", "Forwarded invitation:")
        if any(subject.startswith(p) for p in gcal_prefixes):
            log(f"    → skipped (Google Calendar notification)")
            continue

        # Stage 1: cheap classify (no full body needed)
        classification = classify_email(client, summary, now_str, grade_label, extracurriculars,
                                        school_name, school_location, kids, team_level)
        mark_seen(seen, summary["id"])

        if not classification:
            continue

        email_type = classification.get("email_type", "ignore")
        priority = classification.get("priority", "IGNORE")
        grade_relevant = classification.get("grade_relevant", True)

        log(f"    → [stage1] type={email_type} priority={priority} — {classification.get('reason', '')}")

        if email_type == "ignore" or priority in ("IGNORE", "LOW") or not grade_relevant:
            continue

        # Stage 2: fetch full body, extract structured data
        full = fetch_full_email(summary["id"])
        email_for_extract = dict(full) if full else dict(summary)

        # If body is short (link-only emails like myschoolapp.com newsletters),
        # fetch the linked page and use that as the body
        body = email_for_extract.get("body") or ""
        if len(body) < 1200:
            url = extract_primary_url(body)
            if url:
                log(f"  Fetching linked content: {url[:80]}...")
                linked_text = fetch_url_text(url)
                if linked_text:
                    email_for_extract["body"] = linked_text
                    log(f"  Fetched {len(linked_text)} chars from linked page")

        extraction = extract_email(client, email_for_extract, email_type, now_str, grade_label,
                                   extracurriculars, school_name, school_location, kids, team_level)
        if not extraction:
            continue

        cat = extraction.get("category", "IGNORE")
        action_desc = extraction.get("action_description") or extraction.get("summary") or ""
        is_recurring = extraction.get("is_recurring", False)

        log(f"    → [stage2] category={cat} recurring={is_recurring}")

        if cat == "IGNORE":
            continue

        if cat == "MEETING_EVENT":
            events = extraction.get("events") or []
            if not events:
                events = [{"title": subject, "datetime_str": None, "location": None, "duration_minutes": 60}]

            for ev in events:
                ev_title = ev.get("title") or subject
                ev_dt = ev.get("datetime_str")
                ev_loc = ev.get("location") or ""
                ev_dur = ev.get("duration_minutes") or 60
                desc = f"From: {sender}\nSubject: {subject}"
                if ev_loc:
                    desc += f"\nLocation: {ev_loc}"
                if action_desc:
                    desc += f"\n\n{action_desc}"

                if ev_dt:
                    # For sports/extracurricular, default to 3pm if no time in email
                    default_t = "15:00" if email_type == "sports_schedule" else "09:00"
                    ok = create_calendar_event(
                        client, ev_title, ev_dt, desc,
                        existing_events, now_str, args.dry_run,
                        duration_minutes=ev_dur,
                        default_time=default_t,
                        attendees=calendar_attendees,
                    )
                    if ok:
                        calendar_created.append(ev_title)
                        notifications.append(f"📅 {ev_title} ({ev_dt})")
                else:
                    notes = f"From: {sender} | {action_desc}"
                    ok = add_reminder(f"Schedule: {ev_title}", notes, args.dry_run)
                    if ok:
                        reminders_created.append(ev_title)
                    notifications.append(f"📅 {ev_title} — check date/time")

        elif cat == "ACTION":
            title = subject
            notes = f"From: {sender} | {action_desc}"
            ok = add_reminder(title, notes, args.dry_run)
            if ok:
                reminders_created.append(title)
            notifications.append(f"✅ {title}")

        elif cat == "FYI" and priority == "HIGH":
            school_domains = ("nuevaschool.org", "instructure.com", "myschoolapp.com",
                              "myschoolemails.com", "parentsquare.com")
            is_school = any(d in sender for d in school_domains) or "nueva" in subject.lower()
            if not is_recurring or is_school:
                notifications.append(f"ℹ️ {subject}")

    save_seen(seen)

    if notifications:
        now_str_short = datetime.now(LA_TZ).strftime("%b %d, %I:%M %p")
        lines = [f"📬 Email digest ({now_str_short}):"] + notifications
        if calendar_created:
            lines.append(f"\nCalendar: " + ", ".join(calendar_created))
        if reminders_created:
            lines.append(f"Reminders: " + ", ".join(reminders_created))
        send_imessage(phone, "\n".join(lines), args.dry_run)
        log(f"iMessage sent with {len(notifications)} item(s).")
    else:
        log("Nothing notable — no iMessage sent.")

    log("Done.")


if __name__ == "__main__":
    main()
