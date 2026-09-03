"""
One-time / idempotent setup script: creates the BigQuery dataset + table used
by the Employee Verification agent, and seeds it with sample data if empty.

Usage:
    python adhoc/setup_employee_bq.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env", override=True)

if os.environ.get("SSL_VERIFY", "").strip().lower() in ("false", "0", "no"):
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[attr-defined]
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    # google-cloud-bigquery uses google.auth.transport.requests.AuthorizedSession
    # (built on requests.Session), which ignores ssl._create_default_https_context
    # and always verifies against certifi's bundle — same corp-proxy issue
    # deploy.py works around for httpx. Patch requests.Session directly too.
    import requests
    _orig_session_request = requests.Session.request

    def _session_request(self, *args, **kwargs):
        kwargs.setdefault("verify", False)
        return _orig_session_request(self, *args, **kwargs)

    requests.Session.request = _session_request  # type: ignore[method-assign]

from google.cloud import bigquery

PROJECT_ID = os.environ.get("PROJECT_ID")
if not PROJECT_ID:
    print("✗ PROJECT_ID is not set in .env")
    sys.exit(1)

LOCATION = os.environ.get("LOCATION", "us-central1")
DATASET_ID = "employee_verification"
TABLE_ID = "employee_records"

SCHEMA = [
    bigquery.SchemaField("employee_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField("title", "STRING"),
    bigquery.SchemaField("department", "STRING"),
    bigquery.SchemaField("address", "STRING"),
    bigquery.SchemaField("phone", "STRING"),
    bigquery.SchemaField("email", "STRING"),
    bigquery.SchemaField("emergency_contact", "STRING"),
    bigquery.SchemaField("emergency_phone", "STRING"),
    bigquery.SchemaField("employment_status", "STRING"),
    bigquery.SchemaField("hire_date", "DATE"),
    bigquery.SchemaField("termination_date", "DATE"),
    bigquery.SchemaField("manager", "STRING"),
    bigquery.SchemaField("salary_band", "STRING"),
    bigquery.SchemaField("verified", "BOOLEAN"),
    bigquery.SchemaField("verified_date", "TIMESTAMP"),
]

SAMPLE_ROWS = [
    {
        "employee_id": "E-1001",
        "name": "John Smith",
        "title": "Software Engineer",
        "department": "Engineering",
        "address": "100 Main St, Anytown, USA",
        "phone": "555-0101",
        "email": "john.smith@example.com",
        "emergency_contact": "Jane Smith",
        "emergency_phone": "555-0102",
        "employment_status": "Active",
        "hire_date": "2021-03-15",
        "termination_date": None,
        "manager": "Alice Johnson",
        "salary_band": "L4",
        "verified": False,
        "verified_date": None,
    },
    {
        "employee_id": "E-1002",
        "name": "Maria Garcia",
        "title": "Product Manager",
        "department": "Product",
        "address": "200 Oak Ave, Anytown, USA",
        "phone": "555-0201",
        "email": "maria.garcia@example.com",
        "emergency_contact": "Carlos Garcia",
        "emergency_phone": "555-0202",
        "employment_status": "Active",
        "hire_date": "2020-07-01",
        "termination_date": None,
        "manager": "Bob Williams",
        "salary_band": "L5",
        "verified": True,
        "verified_date": "2026-01-15T10:00:00Z",
    },
    {
        "employee_id": "E-1003",
        "name": "Raj Patel",
        "title": "Data Analyst",
        "department": "Analytics",
        "address": "300 Pine Rd, Anytown, USA",
        "phone": "555-0301",
        "email": "raj.patel@example.com",
        "emergency_contact": "Priya Patel",
        "emergency_phone": "555-0302",
        "employment_status": "Active",
        "hire_date": "2022-11-01",
        "termination_date": None,
        "manager": "Alice Johnson",
        "salary_band": "L3",
        "verified": False,
        "verified_date": None,
    },
    {
        "employee_id": "E-1004",
        "name": "Emily Chen",
        "title": "HR Specialist",
        "department": "Human Resources",
        "address": "400 Cedar Ln, Anytown, USA",
        "phone": "555-0401",
        "email": "emily.chen@example.com",
        "emergency_contact": "David Chen",
        "emergency_phone": "555-0402",
        "employment_status": "Active",
        "hire_date": "2019-05-20",
        "termination_date": None,
        "manager": "Bob Williams",
        "salary_band": "L4",
        "verified": True,
        "verified_date": "2026-02-01T09:30:00Z",
    },
    {
        "employee_id": "E-1005",
        "name": "David Lee",
        "title": "Senior Software Engineer",
        "department": "Engineering",
        "address": "500 Birch Blvd, Anytown, USA",
        "phone": "555-0501",
        "email": "david.lee@example.com",
        "emergency_contact": "Susan Lee",
        "emergency_phone": "555-0502",
        "employment_status": "Active",
        "hire_date": "2018-09-10",
        "termination_date": None,
        "manager": "Alice Johnson",
        "salary_band": "L5",
        "verified": False,
        "verified_date": None,
    },
    {
        "employee_id": "E-1006",
        "name": "Sarah Kim",
        "title": "Marketing Manager",
        "department": "Marketing",
        "address": "600 Elm St, Anytown, USA",
        "phone": "555-0601",
        "email": "sarah.kim@example.com",
        "emergency_contact": "James Kim",
        "emergency_phone": "555-0602",
        "employment_status": "Active",
        "hire_date": "2021-01-05",
        "termination_date": None,
        "manager": "Bob Williams",
        "salary_band": "L4",
        "verified": True,
        "verified_date": "2026-03-01T14:00:00Z",
    },
]


def main() -> None:
    print()
    print("=" * 70)
    print("  Employee Verification — BigQuery Setup")
    print("=" * 70)
    print(f"  Project:  {PROJECT_ID}")
    print(f"  Dataset:  {DATASET_ID}")
    print(f"  Table:    {TABLE_ID}")
    print(f"  Location: {LOCATION}")
    print("=" * 70)
    print()

    client = bigquery.Client(project=PROJECT_ID)

    print("  ⏳ Creating dataset...")
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset_ref.location = LOCATION
    dataset = client.create_dataset(dataset_ref, exists_ok=True)
    print(f"  ✓ Dataset '{DATASET_ID}' ready (location: {dataset.location})")

    print("  ⏳ Creating table...")
    table_ref = bigquery.Table(f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}", schema=SCHEMA)
    table = client.create_table(table_ref, exists_ok=True)
    print(f"  ✓ Table '{TABLE_ID}' ready ({len(table.schema)} columns)")

    print("  ⏳ Checking for existing data...")
    count_query = f"SELECT COUNT(*) as cnt FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`"
    row_count = list(client.query(count_query).result())[0].cnt

    if row_count > 0:
        print(f"  ⚠ Table already has {row_count} rows — skipping data load.")
        print(f"    To reload, run:")
        print(f"      bq query --use_legacy_sql=false 'DELETE FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}` WHERE TRUE'")
        print(f"    Then re-run this script.")
    else:
        print("  ⏳ Loading sample data...")
        errors = client.insert_rows_json(table, SAMPLE_ROWS)
        if errors:
            print(f"  ✗ Errors inserting rows: {errors}")
            sys.exit(1)
        print(f"  ✓ Inserted {len(SAMPLE_ROWS)} sample employee records")

    print()
    print("  ✓ BigQuery setup complete!")
    print()
    print("  Verify with:")
    print(f"    bq query --use_legacy_sql=false \\")
    print(f"      'SELECT employee_id, name, department FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`'")
    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
