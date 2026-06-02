#!/usr/bin/env bash
# Wrapper: run yass-report.py under its venv (matplotlib/openpyxl/pyyaml).
exec /home/gruszecm/workspace/esa/.tools/reportvenv/bin/python \
  "$(dirname "$0")/yass-report.py" "$@"
