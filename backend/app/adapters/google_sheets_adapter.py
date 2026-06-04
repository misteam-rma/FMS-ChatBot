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

    @staticmethod
    def _cell_value(row: List[str], index: int) -> str:
        return str(row[index]).strip() if index < len(row) else ""

    @staticmethod
    def _column_letter(index: int) -> str:
        return gspread.utils.rowcol_to_a1(1, index + 1).rstrip("1")

    def _detect_header_row_index(self, values: List[List[str]]) -> int:
        """Find the real header row in FMS-style sheets with title rows above headers."""
        best_index = 0
        best_score = -1
        expected_headers = {
            "client name": 20,
            "job code": 16,
            "client job code": 16,
            "doer": 12,
            "planned": 8,
            "actual": 8,
            "status": 8,
            "url": 6,
            "remark": 6,
            "timestamp": 5,
        }

        for index, row in enumerate(values[:20]):
            normalized_cells = [str(cell).strip().lower() for cell in row]
            non_empty_count = sum(1 for cell in normalized_cells if cell)
            score = min(non_empty_count, 12)

            for cell in normalized_cells:
                score += expected_headers.get(cell, 0)

            if score > best_score:
                best_score = score
                best_index = index

        return best_index

    def _build_unique_headers(self, values: List[List[str]], header_index: int) -> List[str]:
        header_row = values[header_index]
        headers = []
        seen: Dict[str, int] = {}
        repeated_workflow_headers = {
            "doer",
            "planned",
            "actual",
            "url",
            "remark",
            "status",
            "group",
        }

        max_width = max(len(row) for row in values) if values else 0
        for col_index in range(max_width):
            base_header = self._cell_value(header_row, col_index)
            if not base_header:
                base_header = f"Column {self._column_letter(col_index)}"

            context_parts = []
            for row_index in range(header_index - 1, -1, -1):
                value = self._cell_value(values[row_index], col_index)
                if value and value.lower() not in {part.lower() for part in context_parts}:
                    context_parts.insert(0, value)

            should_prefix = (
                base_header.lower() in repeated_workflow_headers
                or base_header in seen
                or base_header.startswith("Column ")
            )
            if should_prefix and context_parts:
                header = " - ".join(context_parts + [base_header])
            else:
                header = base_header

            count = seen.get(header, 0)
            seen[header] = count + 1
            if count:
                header = f"{header} ({count + 1})"

            headers.append(header)

        return headers

    def _records_from_values(self, values: List[List[str]]) -> List[Dict[str, Any]]:
        if not values:
            return []

        header_index = self._detect_header_row_index(values)
        headers = self._build_unique_headers(values, header_index)
        records = []

        for row in values[header_index + 1:]:
            if not any(str(cell).strip() for cell in row):
                continue

            record = {}
            for col_index, header in enumerate(headers):
                value = self._cell_value(row, col_index)
                if value:
                    record[header] = value

            if record:
                records.append(record)

        return records

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
        try:
            return ws.get_all_records()
        except Exception as exc:
            error_text = str(exc).lower()
            duplicate_header_error = "header row" in error_text and "duplicates" in error_text
            if not duplicate_header_error:
                raise

            print(
                f"[GOOGLE SHEETS] ⚠️ Falling back to flexible parser for worksheet "
                f"'{ws.title}' because headers are not unique."
            )
            return self._records_from_values(ws.get_all_values())

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
