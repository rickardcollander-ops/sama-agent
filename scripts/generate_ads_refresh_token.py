#!/usr/bin/env python3
"""
Generate a Google Ads OAuth2 refresh token.

This script walks you through the OAuth2 flow to generate a refresh token
for the Google Ads API. Use this when switching Google accounts or when
your refresh token has expired.

Prerequisites:
  - GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET must be set in .env.local
    (or passed as arguments)

Usage:
  python scripts/generate_ads_refresh_token.py

The script will:
  1. Open a browser for Google OAuth consent (sign in with rickard.collander@gmail.com)
  2. You paste the authorization code back
  3. It exchanges it for a refresh token
  4. Prints the token so you can update .env.local / Railway
"""

import os
import sys
import webbrowser
from urllib.parse import urlencode

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"

# Google Ads API scope
SCOPES = "https://www.googleapis.com/auth/adwords"


def get_credentials():
    """Load client credentials from .env.local or environment."""
    try:
        from dotenv import load_dotenv
        load_dotenv(".env.local")
    except ImportError:
        pass

    client_id = os.getenv("GOOGLE_ADS_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_ADS_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("ERROR: GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET must be set.")
        print("Set them in .env.local or as environment variables.")
        sys.exit(1)

    return client_id, client_secret


def generate_auth_url(client_id: str) -> str:
    """Generate the OAuth2 authorization URL."""
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "login_hint": "rickard.collander@gmail.com",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(client_id: str, client_secret: str, auth_code: str) -> dict:
    """Exchange authorization code for refresh token."""
    import httpx

    resp = httpx.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    })

    if resp.status_code != 200:
        print(f"ERROR: Token exchange failed: {resp.status_code}")
        print(resp.text)
        sys.exit(1)

    return resp.json()


def main():
    print("=" * 60)
    print("  Google Ads OAuth2 Refresh Token Generator")
    print("  Account: rickard.collander@gmail.com")
    print("=" * 60)
    print()

    client_id, client_secret = get_credentials()

    auth_url = generate_auth_url(client_id)

    print("1. Opening browser for Google sign-in...")
    print(f"   Sign in with: rickard.collander@gmail.com")
    print()
    print(f"   If the browser doesn't open, visit this URL manually:")
    print(f"   {auth_url}")
    print()

    webbrowser.open(auth_url)

    auth_code = input("2. Paste the authorization code here: ").strip()

    if not auth_code:
        print("ERROR: No authorization code provided.")
        sys.exit(1)

    print()
    print("3. Exchanging code for refresh token...")

    token_data = exchange_code_for_token(client_id, client_secret, auth_code)

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("ERROR: No refresh token in response. Did you use prompt=consent?")
        print(f"Response: {token_data}")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  SUCCESS! Here is your new refresh token:")
    print("=" * 60)
    print()
    print(f"  GOOGLE_ADS_REFRESH_TOKEN={refresh_token}")
    print()
    print("  Next steps:")
    print("  1. Update GOOGLE_ADS_REFRESH_TOKEN in .env.local (local dev)")
    print("  2. Update GOOGLE_ADS_REFRESH_TOKEN on Railway (production)")
    print()


if __name__ == "__main__":
    main()
