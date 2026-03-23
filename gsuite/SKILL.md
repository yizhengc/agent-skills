---
name: gsuite
description: This skill should be used when the user asks to read emails, check Gmail, read their inbox, look up messages, check their calendar, see upcoming events, find meetings, check their schedule, or do anything related to Google Workspace, Gmail, or Google Calendar.
version: 1.0.0
---

# GSuite — Gmail & Google Calendar

## Overview

Access Gmail and Google Calendar using bundled Python scripts. Scripts are in:
`~/.claude/skills/gsuite/scripts/`

Assign this path to `SCRIPTS` for brevity:
```
SCRIPTS=~/.claude/skills/gsuite/scripts
```

---

## First-Time Setup

Before using any GSuite features, check if the user is authenticated:

```bash
ls ~/.config/gsuite-skill/token.json 2>/dev/null && echo "authenticated" || echo "not authenticated"
```

If not authenticated, guide the user through setup:

### Step 1: Install dependencies

```bash
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

### Step 2: Get Google API credentials

Direct the user to:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable **Gmail API** and **Google Calendar API** (APIs & Services > Library)
4. Go to APIs & Services > Credentials > Create Credentials > OAuth 2.0 Client ID
5. Choose **Desktop app**, give it a name, click Create
6. Download the JSON file and save it to `~/.config/gsuite-skill/credentials.json`

```bash
mkdir -p ~/.config/gsuite-skill
# User places credentials.json here:
# ~/.config/gsuite-skill/credentials.json
```

### Step 3: Authenticate

```bash
python $SCRIPTS/setup_auth.py
```

This opens a browser for Google sign-in and saves the token.

---

## Reading Email

### List recent inbox messages

```bash
python $SCRIPTS/read_email.py --max 10
```

Output is JSON with fields: `id`, `from`, `subject`, `date`, `snippet`.

### Search emails

Use Gmail search operators:

```bash
# Unread emails
python $SCRIPTS/read_email.py --query "is:unread" --max 20

# From a specific sender
python $SCRIPTS/read_email.py --query "from:boss@company.com"

# With attachment
python $SCRIPTS/read_email.py --query "has:attachment"

# Last 7 days
python $SCRIPTS/read_email.py --query "newer_than:7d"

# Subject contains keyword
python $SCRIPTS/read_email.py --query "subject:invoice"
```

### Read a full email

```bash
python $SCRIPTS/read_email.py --id MESSAGE_ID
```

Output includes: `from`, `to`, `subject`, `date`, `body` (up to 5000 chars).

### List Gmail labels/folders

```bash
python $SCRIPTS/read_email.py --labels
```

---

## Reading Calendar

### Upcoming events (next 7 days)

```bash
python $SCRIPTS/read_calendar.py
```

### Custom time range

```bash
# Next 30 days
python $SCRIPTS/read_calendar.py --days 30

# Just today
python $SCRIPTS/read_calendar.py --days 1
```

### List all calendars

```bash
python $SCRIPTS/read_calendar.py --calendars
```

### Events from a specific calendar

```bash
python $SCRIPTS/read_calendar.py --calendar-id CALENDAR_ID
```

Calendar output fields: `summary`, `start`, `end`, `location`, `description`, `attendees`, `hangoutLink`.

---

## Timezone

The user is on the **US West Coast (`America/Los_Angeles`)**, which is PST (UTC-8) in winter and PDT (UTC-7) during daylight saving time. When the user says "today", "this morning", "tonight", etc., interpret those relative to their current local time in `America/Los_Angeles` — do not use UTC.

To get the current local date in that timezone, run:
```bash
python3 -c "from datetime import datetime; import zoneinfo; tz = zoneinfo.ZoneInfo('America/Los_Angeles'); now = datetime.now(tz); print(now.strftime('%Y/%m/%d'), now.tzname())"
```

For Gmail date queries scoped to "today", use `after:YYYY/MM/DD before:YYYY/MM/DD` with the local LA date.

---

## Common Workflows

### "What's in my inbox?"

Run list with default 10 messages, then summarize the subjects, senders, and snippets for the user.

### "Any emails from X?"

Use `--query "from:X"` and present matching messages.

### "What's on my calendar this week?"

Run `read_calendar.py --days 7`, then present events in chronological order with time, title, and location.

### "Do I have any meetings today?"

Run `read_calendar.py --days 1`, filter events for today's date, and summarize.

### "Read that email from John about the project"

1. Search: `--query "from:john subject:project"`
2. Pick the right message ID from results
3. Read full content with `--id MESSAGE_ID`

---

## Troubleshooting

**Token expired**: The scripts refresh tokens automatically. If refresh fails, re-run `setup_auth.py`.

**Permission denied / Insufficient scopes**: Delete `~/.config/gsuite-skill/token.json` and re-run `setup_auth.py`.

**API not enabled**: Ensure Gmail API and Calendar API are enabled in the Google Cloud Console for the project that owns the credentials.

**Missing dependencies**: Run `pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client`.
