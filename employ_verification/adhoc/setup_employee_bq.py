"""
Adhoc Setup Script — Employee Verification BigQuery Table

Creates the BigQuery dataset and table required by the Employee Verification agent,
and loads mock employee data for testing.

Run this ONCE before deploying the employee_verification agent in a new project:
    python adhoc/setup_employee_bq.py

Prerequisites:
    - PROJECT_ID set in your .env file
    - BigQuery API enabled: gcloud services enable bigquery.googleapis.com
    - Authenticated: gcloud auth application-default login

What it creates:
    - Dataset: employee_verification
    - Table:   employee_records
    - Data:    6 mock employee records
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery

# Load .env from project root (one level up from adhoc/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

PROJECT_ID = os.environ.get("PROJECT_ID")
if not PROJECT_ID:
    print("✗ PROJECT_ID is not set. Add it to your .env file.")
    sys.exit(1)

DATASET_ID = "employee_verification"
TABLE_ID = "employee_records"
FULL_TABLE = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

# BigQuery location — use 'US' (multi-region) for broad compatibility
BQ_LOCATION = "US"

SCHEMA = [
    bigquery.SchemaField("employee_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("title", "STRING"),
    bigquery.SchemaField("department", "STRING"),
    bigquery.SchemaField("location", "STRING"),
    bigquery.SchemaField("address", "STRING"),
    bigquery.SchemaField("phone", "STRING"),
    bigquery.SchemaField("email", "STRING"),
    bigquery.SchemaField("emergency_contact", "STRING"),
    bigquery.SchemaField("emergency_phone", "STRING"),
    bigquery.SchemaField("manager", "STRING"),
    bigquery.SchemaField("employment_status", "STRING"),
    bigquery.SchemaField("hire_date", "DATE"),
    bigquery.SchemaField("termination_date", "DATE"),
    bigquery.SchemaField("salary_band", "STRING"),
    bigquery.SchemaField("verified", "BOOLEAN"),
    bigquery.SchemaField("verified_date", "TIMESTAMP"),
]

MOCK_DATA = [
    {
        "employee_id": "E-1001",
        "name": "John Smith",
        "title": "Senior Financial Analyst",
        "department": "Finance",
        "location": "New York",
        "address": "456 Oak Avenue, Apt 12B, New York, NY 10001",
        "phone": "(555) 123-4567",
        "email": "john.smith@kpmg-demo.com",
        "emergency_contact": "Jane Smith",
        "emergency_phone": "(555) 987-6543",
        "manager": "Sarah Chen",
        "employment_status": "Active",
        "hire_date": "2021-03-15",
        "termination_date": None,
        "salary_band": "Band 4",
        "verified": False,
        "verified_date": None,
    },
    {
        "employee_id": "E-1002",
        "name": "Maria Garcia",
        "title": "Tax Consultant",
        "department": "Tax Advisory",
        "location": "Chicago",
        "address": "789 Elm Street, Suite 300, Chicago, IL 60601",
        "phone": "(555) 234-5678",
        "email": "maria.garcia@kpmg-demo.com",
        "emergency_contact": "Carlos Garcia",
        "emergency_phone": "(555) 876-5432",
        "manager": "David Kim",
        "employment_status": "Active",
        "hire_date": "2019-08-01",
        "termination_date": None,
        "salary_band": "Band 5",
        "verified": False,
        "verified_date": None,
    },
    {
        "employee_id": "E-1003",
        "name": "Raj Patel",
        "title": "IT Security Specialist",
        "department": "Technology",
        "location": "San Francisco",
        "address": "123 Pine Road, San Francisco, CA 94102",
        "phone": "(555) 345-6789",
        "email": "raj.patel@kpmg-demo.com",
        "emergency_contact": "Priya Patel",
        "emergency_phone": "(555) 765-4321",
        "manager": "Lisa Wong",
        "employment_status": "Active",
        "hire_date": "2022-01-10",
        "termination_date": None,
        "salary_band": "Band 4",
        "verified": False,
        "verified_date": None,
    },
    {
        "employee_id": "E-1004",
        "name": "Emily Johnson",
        "title": "Audit Manager",
        "department": "Audit & Assurance",
        "location": "Dallas",
        "address": "321 Maple Drive, Dallas, TX 75201",
        "phone": "(555) 456-7890",
        "email": "emily.johnson@kpmg-demo.com",
        "emergency_contact": "Michael Johnson",
        "emergency_phone": "(555) 654-3210",
        "manager": "Robert Williams",
        "employment_status": "Active",
        "hire_date": "2018-06-20",
        "termination_date": None,
        "salary_band": "Band 6",
        "verified": True,
        "verified_date": "2025-12-01T10:30:00Z",
    },
    {
        "employee_id": "E-1005",
        "name": "David Lee",
        "title": "Advisory Consultant",
        "department": "Advisory",
        "location": "Los Angeles",
        "address": "567 Cedar Lane, Los Angeles, CA 90001",
        "phone": "(555) 567-8901",
        "email": "david.lee@kpmg-demo.com",
        "emergency_contact": "Susan Lee",
        "emergency_phone": "(555) 543-2109",
        "manager": "Jennifer Martinez",
        "employment_status": "On Leave",
        "hire_date": "2020-11-05",
        "termination_date": None,
        "salary_band": "Band 3",
        "verified": False,
        "verified_date": None,
    },
    {
        "employee_id": "E-1006",
        "name": "Sarah Williams",
        "title": "HR Business Partner",
        "department": "Human Resources",
        "location": "Atlanta",
        "address": "890 Birch Boulevard, Atlanta, GA 30301",
        "phone": "(555) 678-9012",
        "email": "sarah.williams@kpmg-demo.com",
        "emergency_contact": "Tom Williams",
        "emergency_phone": "(555) 432-1098",
        "manager": "Patricia Brown",
        "employment_status": "Active",
        "hire_date": "2017-02-14",
        "termination_date": None,
        "salary_band": "Band 5",
        "verified": False,
        "verified_date": None,
    },
]


def _get_project_number(project_id: str) -> str | None:
    """Resolve project ID to project number for IAM instructions."""
    try:
        from google.auth import default
        from google.auth.transport.requests import Request
        import requests as req

        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}"
        resp = req.get(url, headers={"Authorization": f"Bearer {credentials.token}"})
        if resp.status_code == 200:
            return resp.json().get("projectNumber")
    except Exception:
        pass
    return None


def main():
    print()
    print("=" * 70)
    print("  Employee Verification — BigQuery Setup")
    print("=" * 70)
    print(f"  Project:  {PROJECT_ID}")
    print(f"  Dataset:  {DATASET_ID}")
    print(f"  Table:    {TABLE_ID}")
    print(f"  Location: {BQ_LOCATION}")
    print("=" * 70)
    print()

    client = bigquery.Client(project=PROJECT_ID)

    # 1. Create dataset
    print("  ⏳ Creating dataset...")
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset_ref.location = BQ_LOCATION
    try:
        dataset = client.create_dataset(dataset_ref, exists_ok=True)
        print(f"  ✓ Dataset '{DATASET_ID}' ready (location: {dataset.location})")
    except Exception as e:
        print(f"  ✗ Error creating dataset: {e}")
        print()
        print("  Make sure the BigQuery API is enabled:")
        print(f"    gcloud services enable bigquery.googleapis.com --project={PROJECT_ID}")
        sys.exit(1)

    # 2. Create table
    print("  ⏳ Creating table...")
    table_ref = bigquery.Table(f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}", schema=SCHEMA)
    try:
        client.create_table(table_ref, exists_ok=True)
        print(f"  ✓ Table '{TABLE_ID}' ready ({len(SCHEMA)} columns)")
    except Exception as e:
        print(f"  ✗ Error creating table: {e}")
        sys.exit(1)

    # 3. Check if data already exists
    print("  ⏳ Checking for existing data...")
    try:
        result = list(client.query(
            f"SELECT COUNT(*) as cnt FROM `{FULL_TABLE}`"
        ).result())
        existing_count = result[0].cnt
        if existing_count > 0:
            print(f"  ⚠ Table already has {existing_count} rows — skipping data load.")
            print(f"    To reload, run:")
            print(f"      bq query --use_legacy_sql=false 'DELETE FROM `{FULL_TABLE}` WHERE TRUE'")
            print(f"    Then re-run this script.")
        else:
            # 4. Load mock data
            print(f"  ⏳ Loading {len(MOCK_DATA)} mock employee records...")
            errors = client.insert_rows_json(FULL_TABLE, MOCK_DATA)
            if errors:
                print(f"  ✗ Errors inserting data: {errors}")
                sys.exit(1)
            print(f"  ✓ Loaded {len(MOCK_DATA)} mock employee records")
    except Exception as e:
        print(f"  ✗ Error checking/loading data: {e}")
        sys.exit(1)

    print()
    print("  ✓ BigQuery setup complete!")
    print()
    print(f"  Verify with:")
    print(f"    bq query --use_legacy_sql=false \\")
    print(f"      'SELECT employee_id, name, department FROM `{FULL_TABLE}`'")
    print()

    # 5. Print IAM instructions
    print("=" * 70)
    print("  NEXT STEP: Grant IAM permissions to the Agent Engine service account")
    print("=" * 70)
    print()

    project_number = _get_project_number(PROJECT_ID)
    sa = f"service-{project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" if project_number else "service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

    if project_number:
        print(f"  Project number: {project_number}")
    else:
        print(f"  Could not resolve project number automatically.")
        print(f"  Run: gcloud projects describe {PROJECT_ID} --format='value(projectNumber)'")
        print(f"  Then replace PROJECT_NUMBER in the commands below.")

    print()
    print(f"  Run these commands to grant the Agent Engine service account access:")
    print()
    for role in ["roles/aiplatform.user", "roles/bigquery.dataViewer", "roles/bigquery.jobUser"]:
        print(f"  gcloud projects add-iam-policy-binding {PROJECT_ID} \\")
        print(f"    --member='serviceAccount:{sa}' \\")
        print(f"    --role='{role}'")
        print()

    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
