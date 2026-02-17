"""
sheets.py — Google Sheets integration using gspread (OAuth2).

Setup steps (one-time, takes ~5 min):
1. Go to https://console.cloud.google.com/
2. Create a project → Enable "Google Sheets API" + "Google Drive API"
3. Create OAuth 2.0 credentials (Desktop App type)
4. Download the credentials JSON → save as credentials.json in this folder
5. Run this file once directly: python sheets.py
   It will open a browser to authorize, then save token.json locally.
   After that, your agent can create/edit sheets without prompts.
"""

import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os
import json
import pandas as pd
from datetime import datetime

# Scopes needed: read/write sheets + create files in Drive
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def get_gspread_client() -> gspread.Client:
    """
    Authenticate with Google and return a gspread client.
    On first run: opens browser for OAuth consent.
    After that: auto-refreshes using saved token.json.
    """
    creds = None

    # Load saved token if it exists
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # If no valid token, do the OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"❌ '{CREDENTIALS_FILE}' not found.\n"
                    "Please follow the setup steps in sheets.py to create Google OAuth credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)

        # Save token for next run
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return gspread.authorize(creds)


def create_listings_sheet(df: pd.DataFrame, location: str, listing_type: str) -> str:
    """
    Creates a new Google Sheet and populates it with property listings.
    Returns the shareable URL of the created spreadsheet.
    """
    client = get_gspread_client()

    # Create a new spreadsheet with a timestamped name
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    sheet_title = f"Real Estate: {location} ({listing_type}) — {timestamp}"
    spreadsheet = client.create(sheet_title)

    # Make it accessible to anyone with the link (view only)
    spreadsheet.share(None, perm_type="anyone", role="reader")

    worksheet = spreadsheet.sheet1
    worksheet.update_title("Listings")

    if df.empty:
        worksheet.update("A1", [["No listings found matching your criteria."]])
        return spreadsheet.url

    # --- Write headers ---
    headers = list(df.columns)
    worksheet.update("A1", [headers])

    # --- Style the header row ---
    worksheet.format("1:1", {
        "backgroundColor": {"red": 0.18, "green": 0.33, "blue": 0.58},
        "textFormat": {
            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
            "bold": True,
            "fontSize": 11,
        },
        "horizontalAlignment": "CENTER",
    })

    # --- Write data rows ---
    rows = df.fillna("N/A").values.tolist()
    worksheet.update(f"A2", rows)

    # --- Format price column as currency (col B = "Price ($)") ---
    num_rows = len(rows) + 1
    price_col_idx = headers.index("Price ($)") + 1 if "Price ($)" in headers else None
    if price_col_idx:
        price_col_letter = chr(64 + price_col_idx)  # 1→A, 2→B, etc.
        worksheet.format(f"{price_col_letter}2:{price_col_letter}{num_rows}", {
            "numberFormat": {"type": "NUMBER", "pattern": "$#,##0"}
        })

    # --- Auto-resize columns ---
    worksheet.columns_auto_resize(0, len(headers))

    # --- Add a summary row at the top ---
    summary = f"Found {len(df)} properties in {location} | Generated {timestamp}"
    worksheet.insert_row([summary], index=1)
    worksheet.format("A1", {
        "textFormat": {"italic": True, "fontSize": 10},
        "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95},
    })
    worksheet.merge_cells(f"A1:{chr(64 + len(headers))}1")

    print(f"✅ Google Sheet created: {spreadsheet.url}")
    return spreadsheet.url


# Run this file directly to test auth setup
if __name__ == "__main__":
    print("Testing Google Sheets auth...")
    client = get_gspread_client()
    print("✅ Auth successful! token.json saved.")

    # Quick test: create a dummy sheet
    test_data = pd.DataFrame({
        "Listing Link": ["https://redfin.com/test"],
        "Price ($)": [450000],
        "Street Address": ["123 Main St"],
        "City": ["Austin"],
        "State": ["TX"],
        "Zip Code": ["78701"],
        "Beds": [3],
        "Baths": [2],
        "Size (sqft)": [1800],
        "Property Type": ["single_family"],
        "Source": ["redfin"],
    })
    url = create_listings_sheet(test_data, "Austin, TX", "for_sale")
    print(f"Test sheet: {url}")
