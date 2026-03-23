#!/usr/bin/env python3
"""
Read Gmail messages.

Usage:
  python read_email.py [--max N] [--query QUERY] [--id MSG_ID] [--labels]
"""
import argparse
import base64
import json
import os
import sys
from email import message_from_bytes
from email.header import decode_header

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = os.path.expanduser("~/.config/gsuite-skill/token.json")
DEFAULT_CREDS = os.path.expanduser("~/.config/gsuite-skill/credentials.json")


def get_credentials():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        print("ERROR: Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    if not os.path.exists(TOKEN_FILE):
        print(f"ERROR: Not authenticated. Run setup_auth.py first.")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def decode_mime_header(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def get_message_body(payload: dict) -> str:
    """Recursively extract plain text body from message payload."""
    mime_type = payload.get("mimeType", "")
    body = payload.get("body", {})
    parts = payload.get("parts", [])

    if mime_type == "text/plain" and body.get("data"):
        return base64.urlsafe_b64decode(body["data"]).decode("utf-8", errors="replace")

    if mime_type == "text/html" and body.get("data") and not parts:
        # Fallback to HTML if no plain text
        return "[HTML content - use a browser to view]"

    for part in parts:
        result = get_message_body(part)
        if result:
            return result

    return ""


def list_emails(service, max_results: int = 10, query: str = "") -> None:
    results = service.users().messages().list(
        userId="me",
        maxResults=max_results,
        q=query or "in:inbox",
    ).execute()

    messages = results.get("messages", [])
    if not messages:
        print("No messages found.")
        return

    output = []
    for msg in messages:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()

        headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
        snippet = full.get("snippet", "")

        output.append({
            "id": msg["id"],
            "from": decode_mime_header(headers.get("From", "")),
            "subject": decode_mime_header(headers.get("Subject", "(no subject)")),
            "date": headers.get("Date", ""),
            "snippet": snippet[:200],
        })

    print(json.dumps(output, indent=2, ensure_ascii=False))


def read_email(service, msg_id: str) -> None:
    full = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
    body = get_message_body(full.get("payload", {}))

    output = {
        "id": msg_id,
        "from": decode_mime_header(headers.get("From", "")),
        "to": decode_mime_header(headers.get("To", "")),
        "subject": decode_mime_header(headers.get("Subject", "(no subject)")),
        "date": headers.get("Date", ""),
        "body": body.strip()[:5000],  # Cap at 5000 chars
        "body_truncated": len(body) > 5000,
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))


def list_labels(service) -> None:
    results = service.users().labels().list(userId="me").execute()
    labels = results.get("labels", [])
    output = [{"id": l["id"], "name": l["name"]} for l in labels]
    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Read Gmail messages")
    parser.add_argument("--max", type=int, default=10, help="Max messages to list (default: 10)")
    parser.add_argument("--query", default="", help="Gmail search query (default: in:inbox)")
    parser.add_argument("--id", help="Read full message by ID")
    parser.add_argument("--labels", action="store_true", help="List all labels/folders")
    args = parser.parse_args()

    try:
        from googleapiclient.discovery import build
    except ImportError:
        print("ERROR: Run: pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    if args.labels:
        list_labels(service)
    elif args.id:
        read_email(service, args.id)
    else:
        list_emails(service, max_results=args.max, query=args.query)


if __name__ == "__main__":
    main()
