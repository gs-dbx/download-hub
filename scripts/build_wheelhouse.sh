#!/usr/bin/env bash
#
# build_wheelhouse.sh — build an offline wheel mirror for the Data Download Hub
# app and (optionally) install it with no network access.
#
# Platform assumptions (MUST match the Databricks Apps runtime):
#   * OS/ABI : linux, manylinux2014_x86_64
#   * Python : CPython 3.11
# Because we target a foreign platform, we require pre-built wheels only
# (--only-binary=:all:); a source-only dependency would fail here rather than
# silently build a host-specific artifact.
#
# The wheelhouse lives at src/app/wheelhouse/ and IS committed to this repo
# (linux/CPython-3.11 wheels). It sits INSIDE the app source dir so the Databricks
# Apps bundle (source_code_path: ../src/app) syncs it with the app; the Apps build
# then installs offline via `--find-links ./wheelhouse` in src/app/requirements.txt.
# Re-run this script to refresh it (e.g. after a dependency bump), then commit.
# Offline install: pip install --no-index --find-links src/app/wheelhouse -r requirements.lock
#
# Usage:
#   ./scripts/build_wheelhouse.sh            # download wheels into src/app/wheelhouse/
#   ./scripts/build_wheelhouse.sh --install  # download, then offline-install
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="${REPO_ROOT}/requirements.lock"
WHEELHOUSE="${REPO_ROOT}/src/app/wheelhouse"

PLATFORM="manylinux2014_x86_64"
PY_VERSION="311"

echo "==> Downloading wheels from ${LOCK} into ${WHEELHOUSE}"
mkdir -p "${WHEELHOUSE}"
"${PYTHON:-python3}" -m pip download \
  -r "${LOCK}" \
  -d "${WHEELHOUSE}" \
  --platform "${PLATFORM}" \
  --platform manylinux_2_17_x86_64 \
  --platform manylinux_2_28_x86_64 \
  --python-version "${PY_VERSION}" \
  --only-binary=:all:

echo "==> Wheelhouse ready: $(ls -1 "${WHEELHOUSE}" | wc -l) wheels in ${WHEELHOUSE}"

if [[ "${1:-}" == "--install" ]]; then
  echo "==> Installing offline (no network) from ${WHEELHOUSE}"
  "${PYTHON:-python3}" -m pip install --no-index --find-links "${WHEELHOUSE}" -r "${LOCK}"
  echo "==> Offline install complete."
else
  echo "To install offline on the air-gapped target, run:"
  echo "    pip install --no-index --find-links src/app/wheelhouse -r requirements.lock"
fi
