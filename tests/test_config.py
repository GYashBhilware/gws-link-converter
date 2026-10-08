"""Config validation tests: no silent defaults for customer-specific values."""

import os
import unittest
from unittest import mock

from app import auth, config


class ConfigTest(unittest.TestCase):

  def test_missing_required_values_fail_clearly(self):
    with mock.patch.dict(os.environ, {"GCP_PROJECT": "", "BQ_SOURCE_TABLE": ""}):
      with self.assertRaisesRegex(config.ConfigError, "GCP_PROJECT, BQ_SOURCE_TABLE.*install.sh"):
        config.check()

  def test_source_table_must_be_fully_qualified(self):
    with mock.patch.dict(os.environ, {"GCP_PROJECT": "p-1", "BQ_SOURCE_TABLE": "100.activity"}):
      with self.assertRaisesRegex(config.ConfigError, "project.dataset.table"):
        config.check()

  def test_numeric_and_cross_project_tables_accepted(self):
    for t in ("cust-prj.100.activity", "other-proj-123.workspace_logs.activity",
              "example.com:legacy-prj.ds.activity"):
      with mock.patch.dict(os.environ, {"GCP_PROJECT": "app-prj", "BQ_SOURCE_TABLE": t}):
        config.check()

  def test_bad_index_dataset_rejected(self):
    with mock.patch.dict(os.environ, {"GCP_PROJECT": "p-1", "BQ_SOURCE_TABLE": "p-1.d.activity",
                                      "BQ_DATASET": "bad-name;DROP"}):
      with self.assertRaises(config.ConfigError):
        config.check()

  def test_title_default_and_override(self):
    with mock.patch.dict(os.environ, {"APP_TITLE": ""}):
      self.assertEqual(config.app_title(), config.DEFAULT_TITLE)
    with mock.patch.dict(os.environ, {"APP_TITLE": "Contoso link finder"}):
      self.assertEqual(config.app_title(), "Contoso link finder")

  def test_iap_mode_requires_allowed_domain(self):
    env = {"AUTH_MODE": "iap", "IAP_AUDIENCE": "/projects/1/locations/r/services/s",
           "ALLOWED_DOMAIN": ""}
    with mock.patch.dict(os.environ, env):
      os.environ.pop("K_SERVICE", None)
      with self.assertRaisesRegex(RuntimeError, "ALLOWED_DOMAIN"):
        auth.check_config()


if __name__ == "__main__":
  unittest.main()
