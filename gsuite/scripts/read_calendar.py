#!/usr/bin/env python3
"""
Read Google Calendar events.

Usage:
  python read_calendar.py [--days N] [--calendars] [--calendar-id ID]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
TOKEN_FILE = os.path.expanduser("~/.config/gsuite-skill/token.json")


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


def list_calendars(service) -> None:
    result = service.calendarList().list().execute()
    calendars = result.get("items", [])
    output = [
        {
            "id": cal["id"],
            "summary": cal.get("summary", ""),
            "primary": cal.get("primary", False),
            "timeZone": cal.get("timeZone", ""),
        }
        for cal in calendars
    ]
    print(json.dumps(output, indent=2, ensure_ascii=False))


def list_events(service, days: int = 7, calendar_id: str = "primary") -> None:
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        maxResults=50,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = result.get("items", [])
    if not events:
        print(json.dumps([], indent=2))
        return

    output = []
    for event in events:
        start = event.get("start", {})
        end = event.get("end", {})
        output.append({
            "id": event["id"],
            "summary": event.get("summary", "(no title)"),
            "start": start.get("dateTime", start.get("date", "")),
            "end": end.get("dateTime", end.get("date", "")),
            "location": event.get("location", ""),
            "description": (event.get("description", "") or "")[:500],
            "organizer": event.get("organizer", {}).get("email", ""),
            "attendees": [
                {"email": a.get("email", ""), "status": a.get("responseStatus", "")}
                for a in event.get("attendees", [])[:10]
            ],
            "hangoutLink": event.get("hangoutLink", ""),
            "status": event.get("status", ""),
        })

    print(json.dumps(output, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Read Google Calendar events")
    parser.add_argument("--days", type=int, default=7,
                        help="Number of days ahead to fetch (default: 7)")
    parser.add_argument("--calendars", action="store_true",
                        help="List all calendars")
    parser.add_argument("--calendar-id", default="primary",
                        help="Calendar ID to fetch events from (default: primary)")
    args = parser.parse_args()

    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    creds = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    if args.calendars:
        list_calendars(service)
    else:
        list_events(service, days=args.days, calendar_id=args.calendar_id)


if __name__ == "__main__":
    main()
