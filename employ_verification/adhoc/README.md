# Adhoc Scripts

One-time setup scripts for preparing your GCP environment before deploying agents.
These are **not** part of the agent framework — run them once to set up prerequisites.

## Scripts

### `setup_employee_bq.py`

Creates the BigQuery dataset and table required by the Employee Verification agent,
and loads mock employee data for testing.

**When to run:** Before deploying the `employee_verification` agent for the first time,
or when setting up in a new GCP project.

**Prerequisites:**
- `PROJECT_ID` set in your `.env` file
- BigQuery API enabled: `gcloud services enable bigquery.googleapis.com`
- Authenticated: `gcloud auth application-default login`

**Usage:**
```bash
python adhoc/setup_employee_bq.py
```

**What it creates:**
- Dataset: `employee_verification` (in your project)
- Table: `employee_records` (17 columns)
- 6 mock employee records for testing

**Verify the data was loaded:**
```bash
bq query --use_legacy_sql=false \
  'SELECT employee_id, name, department FROM `YOUR_PROJECT.employee_verification.employee_records`'
```

---

## IAM Permissions Required

After deploying the agent to Agent Engine, grant the Agent Engine service account
access to BigQuery and Vertex AI in your project:

```bash
# Find your project number
gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)"

# Grant required roles (replace PROJECT_NUMBER with the number above)
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataViewer"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:service-PROJECT_NUMBER@gcp-sa-aiplatform-re.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"
```

> **Note:** The `setup_employee_bq.py` script will print the exact commands with your
> project number filled in after it runs successfully.
