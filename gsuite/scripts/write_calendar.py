#!/usr/bin/env python3
"""
Create Google Calendar events.

Usage:
  python write_calendar.py --title "Event" --datetime "March 25 at 3pm" [--duration 60] [--description "..."]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]
TOKEN_FILE = os.path.expanduser("~/.config/gsuite-skill/token.json")
CONFIG_FILE = os.path.expanduser("~/.config/gsuite-skill/agent_config.json")


def _load_timezone() -> ZoneInfo:
    try:
        with open(CONFIG_FILE) as f:
            return ZoneInfo(json.load(f).get("timezone", "America/Los_Angeles"))
    except Exception:
        return ZoneInfo("America/Los_Angeles")


LA_TZ = _load_timezone()


def get_credentials():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        print("ERROR: Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    if not os.path.exists(TOKEN_FILE):
        print("ERROR: Not authenticated. Run setup_auth.py first.")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def parse_datetime_with_claude(datetime_str: str) -> datetime:
    """Use Claude Haiku to parse natural language datetime into ISO 8601."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: Run: pip install anthropic")
        sys.exit(1)

    now = datetime.now(LA_TZ)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[{
            "role": "user",
            "content": (
                f"Today is {now.strftime('%Y-%m-%d %H:%M %Z')} ({LA_TZ}). "
                f"Convert this to ISO 8601 with timezone offset: '{datetime_str}'. "
                "Output ONLY the datetime string, e.g.: 2025-03-25T15:00:00-07:00"
            )
        }]
    )
    dt_str = msg.content[0].text.strip()
    return datetime.fromisoformat(dt_str)


def create_event(service, title: str, start_dt: datetime, duration_minutes: int,
                 description: str, calendar_id: str, attendees: list = None):
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    event_body = {
        "summary": title,
        "description": description,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": str(LA_TZ)},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": str(LA_TZ)},
    }
    if attendees:
        event_body["attendees"] = [{"email": a} for a in attendees]
    created = service.events().insert(calendarId=calendar_id, body=event_body).execute()
    return {
        "id": created["id"],
        "title": created["summary"],
        "start": created["start"]["dateTime"],
        "end": created["end"]["dateTime"],
        "link": created.get("htmlLink", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Create or delete a Google Calendar event")
    parser.add_argument("--title", help="Event title (required for create)")
    parser.add_argument("--datetime", dest="datetime_str", help="Event datetime (natural language), parsed via Claude")
    parser.add_argument("--iso-datetime", dest="iso_datetime", help="Event datetime in ISO 8601 format (preferred)")
    parser.add_argument("--duration", type=int, default=60, help="Duration in minutes (default: 60)")
    parser.add_argument("--description", default="", help="Event description")
    parser.add_argument("--calendar-id", default="primary", help="Calendar ID (default: primary)")
    parser.add_argument("--attendees", default="", help="Comma-separated attendee emails")
    parser.add_argument("--delete-id", dest="delete_id", help="Delete event by ID instead of creating")
    args = parser.parse_args()

    if not args.delete_id and not args.title:
        print("ERROR: provide --title or --delete-id")
        sys.exit(1)
    if not args.delete_id and not args.datetime_str and not args.iso_datetime:
        print("ERROR: provide --datetime or --iso-datetime")
        sys.exit(1)

    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    creds = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    if args.delete_id:
        try:
            service.events().delete(calendarId=args.calendar_id, eventId=args.delete_id).execute()
            print(json.dumps({"deleted": args.delete_id}))
        except Exception as e:
            print(f"ERROR deleting calendar event: {e}")
            sys.exit(1)
        return

    try:
        if args.iso_datetime:
            start_dt = datetime.fromisoformat(args.iso_datetime)
        else:
            start_dt = parse_datetime_with_claude(args.datetime_str)
    except Exception as e:
        print(f"ERROR: Could not parse datetime: {e}")
        sys.exit(1)

    attendees = [a.strip() for a in args.attendees.split(",") if a.strip()] if args.attendees else []

    try:
        result = create_event(
            service=service,
            title=args.title,
            start_dt=start_dt,
            duration_minutes=args.duration,
            description=args.description,
            calendar_id=args.calendar_id,
            attendees=attendees,
        )
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"ERROR creating calendar event: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
