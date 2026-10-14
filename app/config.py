"""Deployment configuration, read from environment variables.

Nothing customer-specific is hardcoded: `install.sh` asks for these values, saves
them in `deploy.env`, and `setup.sh` passes them to Cloud Run as env vars.

Required:
  GCP_PROJECT       project that runs BigQuery jobs and holds the index dataset
  BQ_SOURCE_TABLE   Workspace export table, `project.dataset.table` (may be in
                    another project)
Optional:
  BQ_DATASET        index dataset in GCP_PROJECT (default migration_lookup)
  BQ_LOCATION       BigQuery location of both datasets (default US)
  APP_TITLE         heading shown in the UI
"""

import os
import re

DEFAULT_TITLE = "LinkFinder"
_TABLE_RE = re.compile(r"^[a-z][a-z0-9.:-]{4,62}\.[A-Za-z0-9_]{1,1024}\.[A-Za-z0-9_$-]{1,1024}$")


class ConfigError(RuntimeError):
  pass


def project() -> str:
  return os.environ.get("GCP_PROJECT", "")


def source_table() -> str:
  return os.environ.get("BQ_SOURCE_TABLE", "")


def index_dataset() -> str:
  return os.environ.get("BQ_DATASET", "migration_lookup")


def location() -> str:
  return os.environ.get("BQ_LOCATION", "US")


def app_title() -> str:
  return os.environ.get("APP_TITLE", "").strip() or DEFAULT_TITLE


def check() -> None:
  """Fail fast with an actionable message if required settings are missing."""
  missing = [k for k, v in (("GCP_PROJECT", project()), ("BQ_SOURCE_TABLE", source_table()))
             if not v]
  if missing:
    raise ConfigError(
        f"Missing required configuration: {', '.join(missing)}. "
        "Run ./install.sh to create deploy.env, or export the variables.")
  if not _TABLE_RE.match(source_table()):
    raise ConfigError(
        f"BQ_SOURCE_TABLE={source_table()!r} must look like project.dataset.table")
  if not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", index_dataset()):
    raise ConfigError(f"BQ_DATASET={index_dataset()!r} is not a valid dataset name")
