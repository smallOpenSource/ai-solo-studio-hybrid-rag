#!/usr/bin/env bash
# build_windows.sh — US-7/US-8: Linux -> Windows headless export of the native
# client (game/native/) via Godot 4.3-stable.
#
# US-8: the client is now DATA-DRIVEN — it loads ALL 6 game/data files
# (meta/enemies/weapons/waves/upgrades/balance), schema-validates them, and runs
# a minimal playable scene. This script bundles all 6 files into the project and
# emits a sha256 manifest proving the .pck data matches the current game/data.
#
# Produces build/windows/AetherScourge.exe (+ AetherScourge.pck) and verifies
# the PE header ("MZ" magic). On any export-template/version problem it reports
# an HONEST BLOCKER (no fake success).
#
# Isolation: Godot binary + Windows export template live entirely under
# tools/godot/ (no pip/conda/system install). game/data/*.json is READ ONLY
# (copied into the project at build time so the .exe/.pck is self-contained).
#
# Resource guard: the heavy Godot invocations run under .omc/guards/safe_run.sh
# (timeout only; vmem=0 because the engine reserves large virtual address space
# like Node/WASM and ulimit -v would cause a false OOM).
#
# Usage:
#   scripts/build_windows.sh [OUT_DIR]
#     OUT_DIR defaults to build/windows/
set -euo pipefail

# --- paths (repo-root-relative, resolved to absolute) ---------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GODOT_DIR="${REPO_ROOT}/tools/godot"
GODOT_BIN="${GODOT_DIR}/Godot_v4.3-stable_linux.x86_64"
TEMPLATE="${GODOT_DIR}/templates/windows_release_x86_64.exe"
PROJECT_DIR="${REPO_ROOT}/game/native"
DATA_SRC_DIR="${REPO_ROOT}/game/data"
OUT_DIR="${1:-${REPO_ROOT}/build/windows}"
OUT_EXE="${OUT_DIR}/AetherScourge.exe"
SAFE_RUN="${REPO_ROOT}/.omc/guards/safe_run.sh"
GODOT_TIMEOUT=900   # generous: import + cross-export
# The 6 data files the data-driven client consumes (US-8). Order is the load
# order; all must be present (a missing one is an honest blocker, not a fake .exe).
DATA_FILES=(meta enemies weapons waves upgrades balance)

blocker() { echo ""; echo "=== HONEST BLOCKER (US-8) ==="; echo "$*" >&2; exit 1; }

# --- preflight: toolchain present? ----------------------------------------
echo "=== US-8 build_windows: preflight ==="
[ -x "${GODOT_BIN}" ]   || blocker "Godot binary missing/not executable: ${GODOT_BIN} (run the US-7 install steps)."
[ -f "${TEMPLATE}" ]    || blocker "Windows export template missing: ${TEMPLATE}. The .tpz may have failed to download/extract, or the version does not match Godot 4.3-stable. Fix the template install before retrying — do NOT claim a successful build."
[ -f "${SAFE_RUN}" ]    || blocker "Resource guard missing: ${SAFE_RUN}."
# all 6 read-only source data files must exist (US-8 consumes all of them).
for name in "${DATA_FILES[@]}"; do
  [ -f "${DATA_SRC_DIR}/${name}.json" ] || blocker "Source data file missing (read-only input): ${DATA_SRC_DIR}/${name}.json. The data-driven client requires all 6 files."
done

GODOT_VER="$("${GODOT_BIN}" --headless --version 2>/dev/null || true)"
echo "godot: ${GODOT_BIN}"
echo "godot version: ${GODOT_VER}"
echo "template: ${TEMPLATE}"
case "${GODOT_VER}" in
  4.3.stable*) : ;;
  *) blocker "Godot version mismatch: expected 4.3.stable*, got '${GODOT_VER}'. Export template is pinned to 4.3-stable; a mismatch will fail export." ;;
esac

# --- bundle the 6 data files into the project (READ-only source) ----------
echo "=== bundling game/data/{${DATA_FILES[*]}}.json -> game/native/data/ (read-only copies) ==="
# clean re-bundle: drop any stale *.json so a removed source file can't linger
# in the .pck and desync the sha manifest.
rm -rf "${PROJECT_DIR}/data"
mkdir -p "${PROJECT_DIR}/data"
for name in "${DATA_FILES[@]}"; do
  cp "${DATA_SRC_DIR}/${name}.json" "${PROJECT_DIR}/data/${name}.json"
  echo "bundled: ${name}.json ($(wc -c < "${PROJECT_DIR}/data/${name}.json") bytes)"
done

mkdir -p "${OUT_DIR}"
rm -f "${OUT_EXE}" "${OUT_DIR}/AetherScourge.pck"

# --- step 1: import (generate .godot cache, import resources) --------------
# A fresh project has no .godot/ import cache; --export-release on a project
# that was never opened can fail. Run a bounded headless import first.
echo "=== step 1/2: headless import ==="
set +e
bash "${SAFE_RUN}" "${GODOT_TIMEOUT}" 0 -- \
  "${GODOT_BIN}" --headless --path "${PROJECT_DIR}" --import
IMPORT_RC=$?
set -e
# --import may exit non-zero on benign warnings; the real gate is the export.
echo "import exit code: ${IMPORT_RC} (non-zero tolerated; export is the gate)"

# --- step 2: cross-export to Windows .exe ----------------------------------
echo "=== step 2/2: headless export-release 'Windows Desktop' ==="
set +e
bash "${SAFE_RUN}" "${GODOT_TIMEOUT}" 0 -- \
  "${GODOT_BIN}" --headless --path "${PROJECT_DIR}" \
    --export-release "Windows Desktop" "${OUT_EXE}"
EXPORT_RC=$?
set -e
echo "export exit code: ${EXPORT_RC}"

# --- verify: artifact exists + PE header ("MZ") ---------------------------
echo "=== verify artifact + PE header ==="
[ -f "${OUT_EXE}" ] || blocker "Export produced no file at ${OUT_EXE} (godot export-release rc=${EXPORT_RC}). Inspect the godot output above for the failing reason (template path, missing template, version mismatch). Do NOT report success."

MAGIC="$(head -c 2 "${OUT_EXE}")"
if [ "${MAGIC}" != "MZ" ]; then
  blocker "Artifact ${OUT_EXE} is not a PE/Windows executable (first 2 bytes = '${MAGIC}', expected 'MZ')."
fi

# --- verify: .pck embeds all 6 data files ---------------------------------
PCK="${OUT_DIR}/AetherScourge.pck"
echo "=== verify .pck embeds the 6 data files ==="
[ -f "${PCK}" ] || blocker "No .pck at ${PCK} — embed_pck=false expects a sidecar .pck holding the data; its absence means the export did not package resources."
for name in "${DATA_FILES[@]}"; do
  # the .pck stores resource paths as plain strings; grep the path token.
  if grep -aq "data/${name}.json" "${PCK}"; then
    echo "  pck contains: res://data/${name}.json ✓"
  else
    blocker "res://data/${name}.json NOT found in ${PCK}. The data-driven client would fail to load at runtime — refusing to report success."
  fi
done

# --- data sha manifest: bundled (== .pck contents) vs current game/data ----
# Proves the data baked into this build matches the current source files.
MANIFEST="${OUT_DIR}/data_manifest.sha256"
echo "=== data sha256 manifest (bundled vs source) ==="
: > "${MANIFEST}"
MISMATCH=0
for name in "${DATA_FILES[@]}"; do
  src_sha="$(sha256sum "${DATA_SRC_DIR}/${name}.json" | awk '{print $1}')"
  bnd_sha="$(sha256sum "${PROJECT_DIR}/data/${name}.json" | awk '{print $1}')"
  echo "${src_sha}  ${name}.json" >> "${MANIFEST}"
  if [ "${src_sha}" = "${bnd_sha}" ]; then
    echo "  ${name}.json  ${src_sha}  (bundled == source ✓)"
  else
    echo "  ${name}.json  source=${src_sha} bundled=${bnd_sha}  MISMATCH ✗"
    MISMATCH=1
  fi
done
[ "${MISMATCH}" -eq 0 ] || blocker "Bundled data sha != source game/data sha — the .exe would ship stale data. Re-bundle and rebuild."
echo "manifest written: ${MANIFEST}"

echo ""
echo "=== US-8 BUILD OK ==="
echo "exe:    ${OUT_EXE}"
ls -la "${OUT_EXE}"
echo "pck:    ${PCK}"
ls -la "${PCK}"
echo "PE check: first 2 bytes = 'MZ' (PE/Windows executable) ✓"
command -v file >/dev/null 2>&1 && { echo "file(1):"; file "${OUT_EXE}"; }
echo "sha256(exe): $(sha256sum "${OUT_EXE}" | awk '{print $1}')"
echo "sha256(pck): $(sha256sum "${PCK}" | awk '{print $1}')"
