#!/usr/bin/env python3
"""
Google OAuth setup script.
Run this once to authenticate and create token.json.

Usage: python setup_auth.py [--credentials /path/to/credentials.json]
"""
import argparse
import os
import sys

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

TOKEN_FILE = os.path.expanduser("~/.config/gsuite-skill/token.json")
DEFAULT_CREDS = os.path.expanduser("~/.config/gsuite-skill/credentials.json")


def setup_auth(credentials_path: str) -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        import json
    except ImportError:
        print("ERROR: Missing dependencies. Run:")
        print("  pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        # Force re-auth if existing token is missing any required scope
        if creds and creds.scopes and not set(SCOPES).issubset(creds.scopes):
            print("Token is missing required scopes — re-authenticating...")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                print(f"ERROR: credentials.json not found at {credentials_path}")
                print()
                print("To get credentials:")
                print("1. Go to https://console.cloud.google.com/")
                print("2. Create a project (or select existing)")
                print("3. Enable Gmail API and Google Calendar API")
                print("4. Go to APIs & Services > Credentials")
                print("5. Create OAuth 2.0 Client ID (Desktop app)")
                print("6. Download the JSON and save it to:")
                print(f"   {DEFAULT_CREDS}")
                sys.exit(1)

            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved to {TOKEN_FILE}")

    print("Authentication successful!")
    print(f"Token: {TOKEN_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set up Google OAuth authentication")
    parser.add_argument("--credentials", default=DEFAULT_CREDS,
                        help=f"Path to credentials.json (default: {DEFAULT_CREDS})")
    args = parser.parse_args()
    setup_auth(args.credentials)
