"""
Auto-setup service for first-time deployment.
Creates company and database connection on first startup.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.models.models import Company, DatabaseConnection, DatabaseType


FINANCE_FMS_SHEETS = [
    "RAW DATA2",
    "Config",
    "WhatsAppUsers",
    "ChatMessages",
    "Form responses 10",
    "RAW DATA",
    "DATA",
    "DB_Format",
    "Report Upload Form",
    "Doer Emails",
    "RUF Help Sheet",
    "Sanction Letter",
    "Form_Record_Responses",
    "Form_Reply_Responses",
    "Query_Master",
    "Client Docs Index",
    "FMS1",
    "FMS2",
    "FMS3",
    "FMS4",
    "NEW DASH",
    "NEW DASH BANK",
    "Post sanction",
    "HELP_SHEET",
    "Status Update",
    "CLIENT DATA",
    "Steps",
    "NEW DASH for pc",
    "Status Dash",
    "Steps Directory",
    "Completed Dash",
    "Agrasen Group",
    "Drop Dash",
    "Dash Help Sheet - DND",
    "Bank & Email ID",
    "Mail Log",
    "Manualy Status Dash",
    "TeamMatrix",
    "TEAM MEMBER",
    "CODE MASTER",
    "StepMatrix",
    "Admin",
]


def finance_fms_schema_map() -> dict:
    child_tables = {
        sheet_name: {"columns": [], "foreign_key_candidate": "Client Job Code"}
        for sheet_name in FINANCE_FMS_SHEETS
        if sheet_name != settings.google_employee_sheet_name
    }
    return {
        "master_table": settings.google_employee_sheet_name,
        "primary_key": "Client Job Code",
        "phone": "Mobile Number",
        "employee_name": "Client Name",
        "categories": {
            "client": ["Client Name", "Client Job Code", "Mobile Number"],
            "loan": [
                "Project Name",
                "Proposal Type",
                "Term Loan Amt (Cr)",
                "CC Amt (Cr)",
                "BG Amt (Cr)",
                "LC Amt (Cr)",
                "OD Amt (Cr)",
                "LAP Amt (Cr)",
                "Total Loan Amount",
            ],
            "team": ["Team Leader", "Team Engaged", "Concerned Person"],
            "document": ["Attachment URL", "Mail Status"],
        },
        "child_tables": child_tables,
    }


async def auto_setup_database(session: AsyncSession):
    """
    Initialize company and database connection if they don't exist.
    Called on every startup - idempotent (safe to run multiple times).
    """
    # Check if company already exists
    result = await session.execute(select(Company).where(Company.id == "company-1"))
    existing_company = result.scalars().first()

    google_sheet_id = settings.google_sheet_id

    if existing_company:
        print("✅ Company already configured - syncing Finance FMS database settings")
        result = await session.execute(
            select(DatabaseConnection).where(DatabaseConnection.company_id == existing_company.id)
        )
        db_connection = result.scalars().first()
        if db_connection:
            db_connection.title = "Google Sheets - Finance FMS"
            db_connection.description = "Finance FMS workbook from Google Sheets"
            db_connection.db_type = DatabaseType.GOOGLE_SHEETS
            db_connection.connection_config = {
                **(db_connection.connection_config or {}),
                "spreadsheet_id": google_sheet_id or (db_connection.connection_config or {}).get("spreadsheet_id", ""),
                "sheet_name": settings.google_employee_sheet_name,
            }
            db_connection.schema_map = finance_fms_schema_map()
            db_connection.is_active = True
            existing_company.name = "Finance FMS"
            existing_company.industry = "Finance"
            await session.commit()
            print("   ✅ Finance FMS connection settings synced")
        else:
            if not google_sheet_id:
                print("   ⚠️ Company exists but no database connection was found and GOOGLE_SHEET_ID is not set")
                return
            db_connection = DatabaseConnection(
                id="db-conn-1",
                company_id=existing_company.id,
                title="Google Sheets - Finance FMS",
                description="Finance FMS workbook from Google Sheets",
                db_type=DatabaseType.GOOGLE_SHEETS,
                connection_config={
                    "spreadsheet_id": google_sheet_id,
                    "sheet_name": settings.google_employee_sheet_name,
                },
                schema_map=finance_fms_schema_map(),
                is_active=True,
            )
            session.add(db_connection)
            existing_company.name = "Finance FMS"
            existing_company.industry = "Finance"
            await session.commit()
            print("   ✅ Finance FMS connection created")
        return

    print("🔧 First startup detected - auto-configuring database...")

    try:
        # Create company
        company = Company(
            id="company-1",
            name="Finance FMS",
            industry="Finance",
            hr_name="Finance Admin",
            hr_email="hr@company.com",
            support_email="support@company.com",
            google_refresh_token="auto-setup",
            is_active=True
        )
        session.add(company)
        await session.flush()
        print("   ✅ Company created: company-1")

        if not google_sheet_id:
            print("   ⚠️  WARNING: GOOGLE_SHEET_ID not set in .env")
            print("   ⚠️  Database connection will not be created")
            await session.commit()
            return

        # Create database connection
        schema_map = finance_fms_schema_map()

        db_connection = DatabaseConnection(
            id="db-conn-1",
            company_id="company-1",
            title="Google Sheets - Finance FMS",
            description="Finance FMS workbook from Google Sheets",
            db_type=DatabaseType.GOOGLE_SHEETS,
            connection_config={
                "spreadsheet_id": google_sheet_id,
                "sheet_name": settings.google_employee_sheet_name,
            },
            schema_map=schema_map,
            is_active=True
        )
        session.add(db_connection)
        await session.commit()
        print("   ✅ Database connection created: db-conn-1")
        print(f"   ✅ Google Sheet ID: {google_sheet_id}")
        print("\n🎉 Auto-setup complete! System is ready to use.")

    except Exception as e:
        await session.rollback()
        print(f"   ❌ Auto-setup failed: {e}")
        raise
