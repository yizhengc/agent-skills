# Agent Skills

Claude Code skills for personal automation. Each skill lives in its own subdirectory with a `SKILL.md` that Claude Code loads automatically.

## Skills

- **gsuite** — Gmail + Google Calendar email triage agent

---

## gsuite — Email Triage Agent

Reads emails every 4 hours, triages them with Claude, creates Google Calendar events, adds macOS Reminders, and sends iMessage notifications for important items.

### Architecture

Two-stage analysis per email:
1. **Stage 1 (Claude Haiku)** — cheap classifier using only headers/snippet → `{email_type, priority, grade_relevant}`
2. **Stage 2 (Claude Sonnet)** — full body extractor, type-aware → `{events, actions, summary}`

Python code is generic; all type-specific logic lives in Claude prompts.

### Prerequisites

- macOS (uses osascript for iMessage + Reminders)
- Python 3.9+ with pip
- Google account
- Anthropic API key

### Setup

#### 1. Install Python dependencies

```bash
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client anthropic
```

#### 2. Create Google Cloud credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable **Gmail API** and **Google Calendar API** (APIs & Services → Library)
4. Go to APIs & Services → Credentials → Create Credentials → **OAuth 2.0 Client ID**
5. Choose **Desktop app**, click Create
6. Download the JSON and save it to:
   ```
   ~/.config/gsuite-skill/credentials.json
   ```
7. Go to **OAuth consent screen** → add your Google account as a **Test user**

#### 3. Create config file

```bash
mkdir -p ~/.config/gsuite-skill
cp gsuite/agent_config.example.json ~/.config/gsuite-skill/agent_config.json
```

Edit `~/.config/gsuite-skill/agent_config.json`:

```json
{
  "imessage_phone": "4151234567",
  "anthropic_api_key": "sk-ant-api03-...",
  "ninth_grade_start_year": 2025,
  "extracurriculars": ["varsity tennis"],
  "calendar_attendees": ["family1@gmail.com", "family2@gmail.com"],
  "school_name": "Your School Name",
  "school_location": "City, State",
  "timezone": "America/Los_Angeles"
}
```

| Field | Description |
|---|---|
| `imessage_phone` | 10-digit US phone number for iMessage notifications |
| `anthropic_api_key` | Your Anthropic API key |
| `ninth_grade_start_year` | Calendar year your kids started 9th grade (auto-advances each year) |
| `extracurriculars` | List of activities — used to prioritize related emails |
| `calendar_attendees` | Emails added as attendees to every created calendar event |
| `school_name` | School name used in Claude prompts |
| `school_location` | City/state used in Claude prompts |
| `timezone` | IANA timezone string, e.g. `America/Los_Angeles` |

#### 4. Authenticate with Google

```bash
python3 ~/.claude/skills/gsuite/scripts/setup_auth.py
```

This opens a browser for Google sign-in and saves the token to `~/.config/gsuite-skill/token.json`.

> **Note:** With Google OAuth in Testing mode, the token expires every 7 days. The cron job below handles automatic renewal — as long as it runs before expiry, no browser interaction is needed.

#### 5. Set up the cron jobs

Add both entries via `crontab -e`:

```
# Email triage — runs every 4 hours (8am, 12pm, 4pm, 8pm)
0 8,12,16,20 * * * /Applications/Xcode.app/Contents/Developer/usr/bin/python3 ~/.claude/skills/gsuite/scripts/email_agent.py >> ~/.config/gsuite-skill/agent.log 2>&1

# OAuth token refresh — runs every Sunday at 7am (before token expires)
0 7 * * 0 /Applications/Xcode.app/Contents/Developer/usr/bin/python3 ~/.claude/skills/gsuite/scripts/setup_auth.py >> ~/.config/gsuite-skill/agent.log 2>&1
```

> Adjust the Python path if needed: `which python3` to find yours.

### Testing

```bash
SCRIPTS=~/.claude/skills/gsuite/scripts

# Dry-run on last 4 hours of email (no actions taken)
python3 $SCRIPTS/email_agent.py --dry-run

# Test with specific query
python3 $SCRIPTS/email_agent.py --query "subject:tennis" --max 3 --dry-run

# Test school emails only
python3 $SCRIPTS/email_agent.py --query "from:nuevaschool.org OR from:instructure.com" --max 3 --dry-run

# Live run (creates real events, sends iMessage)
python3 $SCRIPTS/email_agent.py --query "subject:tennis" --max 2
```

### Logs

```bash
tail -f ~/.config/gsuite-skill/agent.log
```

### Files

| File | Description |
|---|---|
| `gsuite/SKILL.md` | Claude Code skill definition |
| `gsuite/scripts/email_agent.py` | Main triage agent |
| `gsuite/scripts/read_email.py` | Gmail API reader |
| `gsuite/scripts/read_calendar.py` | Google Calendar reader |
| `gsuite/scripts/write_calendar.py` | Calendar event create/delete |
| `gsuite/scripts/setup_auth.py` | OAuth setup + token refresh |
| `gsuite/agent_config.example.json` | Config template |
| `~/.config/gsuite-skill/agent_config.json` | Your config (not committed) |
| `~/.config/gsuite-skill/credentials.json` | Google OAuth credentials (not committed) |
| `~/.config/gsuite-skill/token.json` | OAuth token (not committed) |
| `~/.config/gsuite-skill/seen_emails.json` | Deduplication state (not committed) |
| `~/.config/gsuite-skill/agent.log` | Run log (not committed) |
