"""tools/install_summary.py: PASS/WARN/FAIL summary files."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROWS = ("prereq\tPASS\tgcloud CLI installed\t\n"
        "preflight\tPASS\tproject accessible\tp-1\n"
        "preflight\tWARN\tCloud Run region\tAPI not enabled yet\n")


def run(rows, code, mode):
  d = tempfile.mkdtemp()
  res, summ = os.path.join(d, "r.tsv"), os.path.join(d, "s.txt")
  with open(res, "w") as f:
    f.write(rows)
  out = subprocess.run([sys.executable, "-m", "tools.install_summary", res, summ, str(code), mode,
                        "logs/x.log"], capture_output=True, text=True, check=True).stdout
  return out, open(summ).read(), json.load(open(os.path.join(d, "s.json")))


class SummaryTest(unittest.TestCase):

  def test_success_with_warning(self):
    out, txt, js = run(ROWS, 0, "install")
    self.assertIn("Result: SUCCESS", txt)
    self.assertEqual(js["counts"], {"PASS": 2, "WARN": 1, "FAIL": 0})
    self.assertIn("SUCCESS", out)
    self.assertIn("Full log: logs/x.log", txt)

  def test_fail_line_gives_next_step(self):
    _, txt, js = run(ROWS + "deploy\tFAIL\tstep iam\texit code 1 after 3s\n", 1, "install")
    self.assertIn("Result: FAILED", txt)
    self.assertIn("What to do: fix 'step iam'", txt)
    self.assertEqual(js["result"], "FAILED")

  def test_nonzero_exit_without_fail_lines_is_failed(self):
    _, txt, _ = run(ROWS, 130, "install")
    self.assertIn("Result: FAILED", txt)
    self.assertIn("exit code 130", txt)

  def test_dry_run_and_stage_order(self):
    _, txt, _ = run("result\tINFO\tnothing was changed\tdry-run\n" + ROWS, 0, "dry-run")
    self.assertIn("Result: DRY RUN OK", txt)
    self.assertLess(txt.index("[prereq]"), txt.index("[preflight]"))
    self.assertLess(txt.index("[preflight]"), txt.index("[result]"))

  def test_missing_results_file_does_not_crash(self):
    d = tempfile.mkdtemp()
    r = subprocess.run([sys.executable, "-m", "tools.install_summary", os.path.join(d, "none"),
                        os.path.join(d, "s.txt"), "1", "install"], capture_output=True, text=True)
    self.assertEqual(r.returncode, 0)
    self.assertIn("FAILED", open(os.path.join(d, "s.txt")).read())


if __name__ == "__main__":
  unittest.main()
