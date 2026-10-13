#!/usr/bin/env python3
"""Print and save the install summary from the results file written during install.sh.

  python3 -m tools.install_summary <results.tsv> <summary.txt> <exit_code> <mode> [log]

Results lines: stage <TAB> PASS|WARN|FAIL|INFO <TAB> check <TAB> detail
Writes a plain-text summary and <summary>.json (also used for the Cloud Logging entry).
Exit status 0 always (reporting must never mask the installer's own exit code).
"""

import json
import sys
import time

COLORS = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m", "INFO": "\033[36m"}
STAGES = ["prereq", "config", "preflight", "deploy", "sync", "verify", "result"]


def load(path):
  rows = []
  try:
    with open(path, encoding="utf-8") as f:
      for line in f:
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 3:
          rows.append((parts + [""])[:4])
  except FileNotFoundError:
    pass
  return rows


def main():
  results, summary_path, code, mode = sys.argv[1:5]
  log = sys.argv[5] if len(sys.argv) > 5 else ""
  code = int(code)
  rows = load(results)
  counts = {k: sum(r[1] == k for r in rows) for k in ("PASS", "WARN", "FAIL")}
  failed = counts["FAIL"] > 0 or code != 0
  overall = "FAILED" if failed else ("DRY RUN OK" if mode == "dry-run" else "SUCCESS")

  width = max([len(r[2]) for r in rows] + [10])
  lines = [f"Link Finder install summary - {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
           f"Mode: {mode}    Result: {overall}    "
           f"({counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail)", ""]
  order = {s: i for i, s in enumerate(STAGES)}
  for stage in sorted({r[0] for r in rows}, key=lambda s: order.get(s, 99)):
    lines.append(f"[{stage}]")
    for st, lvl, name, detail in (r for r in rows if r[0] == stage):
      lines.append(f"  {lvl:<4}  {name:<{width}}  {detail}")
    lines.append("")
  if failed:
    first = next((r for r in rows if r[1] == "FAIL"), None)
    lines.append("What to do: " + (f"fix '{first[2]}' ({first[3]})" if first else
                                   f"the installer stopped with exit code {code}") +
                 ", then run ./install.sh again (your answers are remembered).")
  if log:
    lines.append(f"Full log: {log}")
  lines.append(f"Summary:  {summary_path}")

  with open(summary_path, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
  with open(summary_path.rsplit(".", 1)[0] + ".json", "w", encoding="utf-8") as f:
    json.dump({"event": "link_finder_install", "mode": mode, "result": overall,
               "exit_code": code, "counts": counts,
               "checks": [{"stage": r[0], "result": r[1], "check": r[2], "detail": r[3]}
                          for r in rows]}, f)

  # Console: same content, colored.
  bar = "=" * 72
  print("\n" + bar)
  for ln in lines:
    for lvl, c in COLORS.items():
      if ln.startswith(f"  {lvl}"):
        ln = ln.replace(lvl, f"{c}{lvl}\033[0m", 1)
        break
    if ln.startswith("Mode:"):
      c = COLORS["FAIL"] if failed else COLORS["PASS"]
      ln = ln.replace(overall, f"\033[1m{c}{overall}\033[0m", 1)
    print(ln)
  print(bar)


if __name__ == "__main__":
  try:
    main()
  except Exception as e:  # noqa: BLE001 - never break the installer's exit path
    print(f"(could not write install summary: {e})", file=sys.stderr)
