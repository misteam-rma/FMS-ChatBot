"""Tool boundary for FMS v2 chat orchestration."""

from app.fms_v2.models import FetchFmsRecordsInput, FetchFmsRecordsOutput
from app.fms_v2.sheets import fetch_fms_records_by_client_code


async def fetch_records_tool(data: FetchFmsRecordsInput) -> FetchFmsRecordsOutput:
    """Validated tool wrapper for fetching FMS records."""

    validated = FetchFmsRecordsInput.model_validate(data)
    return await fetch_fms_records_by_client_code(validated)
