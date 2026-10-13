"""Preflight gcloud checks must never hang or crash (fresh projects, slow gcloud)."""

import subprocess
import unittest
from unittest import mock

from tools import preflight


def _run(rc=0, out="", err=""):
  return mock.Mock(returncode=rc, stdout=out, stderr=err)


class GcloudTest(unittest.TestCase):

  def test_non_interactive_flags(self):
    with mock.patch("subprocess.run", return_value=_run(0, "x")) as run:
      preflight._gcloud("run", "regions", "list")
    args, kwargs = run.call_args
    self.assertIn("--quiet", args[0])
    self.assertIs(kwargs["stdin"], subprocess.DEVNULL)

  def test_timeout_is_reported_not_raised(self):
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gcloud", 60)):
      rc, out, err = preflight._gcloud("run", "regions", "list")
    self.assertEqual(rc, 124)
    self.assertIn("timed out", err)


class RegionTest(unittest.TestCase):

  def check(self, run_result, region="us-central1"):
    with mock.patch("subprocess.run", **run_result):
      return preflight.check_region("p-1", region)

  def test_valid_region(self):
    self.assertEqual(self.check({"return_value": _run(0, "us-central1\neurope-west1")})[0], "PASS")

  def test_unknown_region(self):
    lvl, _ = self.check({"return_value": _run(0, "us-central1")}, region="europe-west9")
    self.assertEqual(lvl, "FAIL")

  def test_malformed_region_fails_without_calling_gcloud(self):
    with mock.patch("subprocess.run") as run:
      self.assertEqual(preflight.check_region("p-1", "mars central")[0], "FAIL")
    run.assert_not_called()

  def test_api_disabled_is_warning(self):
    lvl, detail = self.check({"return_value": _run(1, "", "reason: SERVICE_DISABLED")})
    self.assertEqual(lvl, "WARN")
    self.assertIn("not enabled yet", detail)

  def test_timeout_is_warning(self):
    lvl, _ = self.check({"side_effect": subprocess.TimeoutExpired("gcloud", 60)})
    self.assertEqual(lvl, "WARN")


class ServiceTest(unittest.TestCase):

  def test_existing_and_new(self):
    with mock.patch("subprocess.run", return_value=_run(0, "img@sha256:x")):
      self.assertEqual(preflight.check_service("p", "r", "s")[0], "WARN")
    with mock.patch("subprocess.run", return_value=_run(1, "", "SERVICE_DISABLED")):
      self.assertEqual(preflight.check_service("p", "r", "s")[0], "PASS")


if __name__ == "__main__":
  unittest.main()
