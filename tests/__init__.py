"""Test bootstrap: neutral placeholder config so modules import without deploy.env.

BigQuery integration tests (RUN_BQ_TESTS=1) need real values; export them first,
e.g. `set -a; . ./deploy.env; set +a`.
"""

import os

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("BQ_SOURCE_TABLE", "test-project.workspace_export.activity")
