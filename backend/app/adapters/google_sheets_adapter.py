"""
Botivate HR Support - Google Sheets Adapter
Implements BaseDatabaseAdapter for Google Sheets.
This is the DEFAULT adapter used for employee data.
"""

import json
import gspread
from google.oauth2.credentials import Credentials
from typing import Any, Dict, List, Optional
from app.adapters.base_adapter import BaseDatabaseAdapter
from app.config import settings


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleSheetsAdapter(BaseDatabaseAdapter):
    """
    Adapter for Google Sheets.
    connection_config expected format:
    {
        "spreadsheet_id": "your-google-sheet-id",
        "sheet_name": "Sheet1"   (optional, defaults to first sheet)
    }
    """

    def __init__(self):
        self.client: Optional[gspread.Client] = None
        self.spreadsheet = None
        self.worksheet = None
        self.worksheet = None
        self._headers_cache: Dict[str, List[str]] = {}

    async def connect(self, config: Dict[str, Any], refresh_token: Optional[str] = None) -> None:
        """Connect to Google Sheets using the HR's OAuth refresh token OR service account JSON."""
        import os

        # For local testing/demo: skip auth if in development mode
        if os.getenv("SKIP_GOOGLE_AUTH") == "true":
            print("[GOOGLE SHEETS] ⚠️ DEMO MODE: Skipping Google authentication")
            # In demo mode, we'll use a mock client that simulates the sheet data
            self.client = None
            self.is_demo_mode = True
            return

        print(f"[GOOGLE SHEETS] 🔌 Connecting to Database using config...")
        raw_spreadsheet_input = config.get("spreadsheet_id", "")
        sheet_name = config.get("sheet_name", None)

        if not raw_spreadsheet_input:
            print("[GOOGLE SHEETS] ❌ ERROR: spreadsheet_id is missing from connection_config.")
            raise ValueError("spreadsheet_id (or a link) is required in connection_config.")

        # Extract ID if a full URL is provided
        spreadsheet_id = raw_spreadsheet_input
        if "spreadsheets/d/" in raw_spreadsheet_input:
            parts = raw_spreadsheet_input.split("spreadsheets/d/")
            if len(parts) > 1:
                spreadsheet_id = parts[1].split("/")[0]

        if not refresh_token:
            refresh_token = config.get("google_refresh_token")

        # Try service account JSON as fallback for local testing
        service_account_json = config.get("google_service_account_json") or settings.google_service_account_json

        if not refresh_token and not service_account_json:
            print("[GOOGLE SHEETS] ❌ ERROR: No OAuth refresh_token or service account JSON provided.")
            raise ValueError("Company must connect their Google Workspace first to access the sheet.")

        # Try OAuth first, then fallback to service account
        try:
            if service_account_json and not refresh_token:
                # Use service account JSON (for local testing)
                try:
                    service_account_dict = json.loads(service_account_json) if isinstance(service_account_json, str) else service_account_json
                    self.client = gspread.service_account_from_dict(service_account_dict)
                    print("[GOOGLE SHEETS] ✓ Using service account credentials from JSON")
                except (json.JSONDecodeError, ValueError) as json_err:
                    print(f"[GOOGLE SHEETS] ⚠️ Failed to parse service account JSON: {json_err}")
                    print("[GOOGLE SHEETS] Trying to load from file: backend/service-account.json")
                    try:
                        self.client = gspread.service_account(filename="service-account.json")
                        print("[GOOGLE SHEETS] ✓ Using service account credentials from file")
                    except Exception as file_err:
                        print(f"[GOOGLE SHEETS] ❌ Failed to load from file: {file_err}")
                        raise ValueError(f"Could not load service account: {json_err}")
            else:
                # Use OAuth refresh token
                credentials = Credentials(
                    None, # Empty access token (force refresh)
                    refresh_token=refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=settings.google_oauth_client_id,
                    client_secret=settings.google_oauth_client_secret
                )
                self.client = gspread.authorize(credentials)
                print("[GOOGLE SHEETS] ✓ Using OAuth credentials")
        except Exception as e:
            print(f"[GOOGLE SHEETS] ❌ ERROR: Could not authorize with Google: {e}")
            raise

        self.spreadsheet = self.client.open_by_key(spreadsheet_id)
        print(f"[GOOGLE SHEETS] ✅ SUCCESS: Connected to Spreadsheet '{self.spreadsheet.title}' (ID: {spreadsheet_id})")

        if sheet_name:
            self.worksheet = self.spreadsheet.worksheet(sheet_name)
            print(f"[GOOGLE SHEETS] 📄 Connected to specific worksheet: '{sheet_name}'")
        else:
            self.worksheet = self.spreadsheet.sheet1
            print(f"[GOOGLE SHEETS] 📄 Connected to default worksheet1: '{self.worksheet.title}'")

        # Cache headers for default worksheet
        self._headers_cache[self.worksheet.title] = self.worksheet.row_values(1)

    def _get_target_worksheet(self, table_name: Optional[str] = None):
        """Helper to get the target worksheet."""
        if not self.spreadsheet:
            raise ConnectionError("Not connected to Google Sheets.")
        
        if table_name:
            return self.spreadsheet.worksheet(table_name)
        elif self.worksheet:
            return self.worksheet
        else:
            raise ConnectionError("No default worksheet connected.")

    async def get_available_tables(self) -> List[str]:
        """Return titles of all worksheets in the Google Sheet."""
        if not self.spreadsheet:
            raise ConnectionError("Not connected to Google Sheets.")
        
        return [ws.title for ws in self.spreadsheet.worksheets()]

    async def get_headers(self, table_name: Optional[str] = None) -> List[str]:
        """Return column headers from row 1 of the target worksheet."""
        ws = self._get_target_worksheet(table_name)
        title = ws.title
        
        if title not in self._headers_cache:
            self._headers_cache[title] = ws.row_values(1)
        return self._headers_cache[title]

    async def get_all_records(self, table_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all records as a list of dicts."""
        ws = self._get_target_worksheet(table_name)
        return ws.get_all_records()

    async def get_record_by_key(self, key_column: str, key_value: str, table_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Find a single record by its primary key column value."""
        print(f"[GOOGLE SHEETS] 🔍 Searching for record where '{key_column}' == '{key_value}' in table '{table_name or 'default'}'...")
        records = await self.get_all_records(table_name)
        print(f"[GOOGLE SHEETS] Iterating over {len(records)} total records to find match...")
        for record in records:
            if str(record.get(key_column, "")).strip().lower() == str(key_value).strip().lower():
                print(f"[GOOGLE SHEETS] ✅ SUCCESS: Record matched and found!")
                return record
        
        print(f"[GOOGLE SHEETS] ❌ FAILED: Record '{key_value}' NOT found after checking {len(records)} rows.")
        return None

    async def get_records_by_filter(self, filters: Dict[str, Any], table_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Filter records matching all key-value pairs in filters."""
        records = await self.get_all_records(table_name)
        results = []
        for record in records:
            match = True
            for fk, fv in filters.items():
                if str(record.get(fk, "")).strip().lower() != str(fv).strip().lower():
                    match = False
                    break
            if match:
                results.append(record)
        return results

    async def update_record(self, key_column: str, key_value: str, updates: Dict[str, Any], table_name: Optional[str] = None) -> bool:
        """Update a specific employee's fields by locating their row.
        If a column in updates doesn't exist, it will be auto-created."""
        ws = self._get_target_worksheet(table_name)

        headers = await self.get_headers(table_name)
        if key_column not in headers:
            raise ValueError(f"Key column '{key_column}' not found in headers.")

        key_col_index = headers.index(key_column) + 1  # gspread is 1-indexed
        cell = ws.find(str(key_value), in_column=key_col_index)

        if not cell:
            return False

        row_number = cell.row
        
        # Build cell data for update
        updates_list = []
        for col_name, value in updates.items():
            if col_name not in headers:
                try:
                    await self.add_column(col_name, table_name=table_name)
                    headers = await self.get_headers(table_name)
                except: continue
            
            if col_name in headers:
                col_index = headers.index(col_name) + 1
                # Format for update: {range: 'A1', values: [[val]]} for v6 compatibility
                col_letter = gspread.utils.rowcol_to_a1(row_number, col_index)
                updates_list.append({
                    'range': col_letter,
                    'values': [[value]]
                })
        
        if updates_list:
            # batch_update is more compatible across gspread versions
            ws.batch_update(updates_list)

        return True

    async def create_record(self, data: Dict[str, Any], table_name: Optional[str] = None) -> bool:
        """Create a new record (row) in the Google Sheet."""
        ws = self._get_target_worksheet(table_name)

        headers = await self.get_headers(table_name)
        
        # Ensure all columns in the new record exist in the sheet
        headers_updated = False
        for col_name in data.keys():
            if col_name not in headers:
                await self.add_column(col_name, table_name=table_name)
                headers_updated = True
        
        if headers_updated:
            headers = await self.get_headers(table_name)

        # Construct the row values in order
        new_row = []
        for h in headers:
            new_row.append(data.get(h, ""))

        # Append to the bottom
        ws.append_row(new_row)
        return True

    async def add_column(self, column_name: str, default_values: Optional[List[Any]] = None, table_name: Optional[str] = None) -> bool:
        """Add a new column at the end of the sheet."""
        ws = self._get_target_worksheet(table_name)

        headers = await self.get_headers(table_name)

        # Check if column already exists
        if column_name in headers:
            return True  # Already exists, no-op

        new_col_index = len(headers) + 1
        
        # Expand grid horizontally if needed
        if new_col_index > ws.col_count:
            ws.add_cols(1)
            
        cells_to_update = [gspread.Cell(row=1, col=new_col_index, value=column_name)]

        # Write default values if provided
        if default_values:
            for i, val in enumerate(default_values):
                cells_to_update.append(gspread.Cell(row=i + 2, col=new_col_index, value=val))

        ws.update_cells(cells_to_update)

        # Refresh headers cache
        self._headers_cache[ws.title] = ws.row_values(1)
        return True

    async def update_column_values(self, column_name: str, key_column: str,
                                    key_value_map: Dict[str, Any], table_name: Optional[str] = None) -> bool:
        """Bulk update a column's values using a mapping of {key_value: new_value}."""
        ws = self._get_target_worksheet(table_name)

        headers = await self.get_headers(table_name)
        if column_name not in headers:
            raise ValueError(f"Column '{column_name}' not found.")
        if key_column not in headers:
            raise ValueError(f"Key column '{key_column}' not found.")

        key_col_idx = headers.index(key_column) + 1
        target_col_idx = headers.index(column_name) + 1

        all_values = ws.col_values(key_col_idx)
        cells_to_update = []

        for row_idx, cell_value in enumerate(all_values):
            if row_idx == 0:
                continue  # Skip header
            clean_val = str(cell_value).strip()
            if clean_val in key_value_map:
                cells_to_update.append(gspread.Cell(row=row_idx + 1, col=target_col_idx, value=key_value_map[clean_val]))

        if cells_to_update:
            ws.update_cells(cells_to_update)

        return True

    async def get_column_values(self, column_name: str, table_name: Optional[str] = None) -> List[Any]:
        """Get all values for a specific column (excluding header)."""
        ws = self._get_target_worksheet(table_name)

        headers = await self.get_headers(table_name)
        if column_name not in headers:
            raise ValueError(f"Column '{column_name}' not found.")

        col_idx = headers.index(column_name) + 1
        all_values = ws.col_values(col_idx)
        return all_values[1:]  # Exclude header
