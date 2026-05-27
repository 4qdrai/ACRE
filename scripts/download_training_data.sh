#!/bin/bash
# ============================================================================
# Download Training Data for ACRE
# ============================================================================
# Downloads and prepares the SCAN dataset for compositional generalization
# experiments. SCAN (Simplified Compositions as Actions in Natural-language)
# tests whether models can generalize to novel compositions of known primitives.
#
# Usage:
#   bash scripts/download_training_data.sh
#
# Datasets downloaded:
#   1. SCAN (Lake & Baroni, 2018) — all splits
#      - Simple split (random 80/20)
#      - Length split (train on short, test on long)
#      - Add-jump split (hold out "jump" compositions)
#   2. COGS (Kim & Linzen, 2020) — optional compositional generalization
#
# Output structure:
#   data/
#   ├── scan/
#   │   ├── simple/
#   │   │   ├── train.txt
#   │   │   └── test.txt
#   │   ├── length/
#   │   │   ├── train.txt
#   │   │   └── test.txt
#   │   └── addprim_jump/
#   │       ├── train.txt
#   │       └── test.txt
#   └── cogs/  (optional)
# ============================================================================

set -euo pipefail

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[DATA]${NC} $1"; }
log_ok()   { echo -e "${GREEN}[OK]${NC}   $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

DATA_DIR="data"
SCAN_DIR="${DATA_DIR}/scan"
SCAN_GITHUB_URL="https://github.com/brendenlake/SCAN/archive/refs/heads/master.zip"
SCAN_ZIP="${DATA_DIR}/scan_master.zip"

# ── Create directories ──────────────────────────────────────────────
mkdir -p "${SCAN_DIR}/simple"
mkdir -p "${SCAN_DIR}/length"
mkdir -p "${SCAN_DIR}/addprim_jump"

# ── Download SCAN ───────────────────────────────────────────────────
if [ -f "${SCAN_DIR}/length/train.txt" ] && [ -f "${SCAN_DIR}/length/test.txt" ]; then
    log_ok "SCAN dataset already downloaded. Skipping."
else
    log_info "Downloading SCAN dataset from GitHub..."
    
    if command -v wget &> /dev/null; then
        wget -q "${SCAN_GITHUB_URL}" -O "${SCAN_ZIP}"
    elif command -v curl &> /dev/null; then
        curl -sL "${SCAN_GITHUB_URL}" -o "${SCAN_ZIP}"
    else
        log_warn "Neither wget nor curl found. Please install one."
        exit 1
    fi
    
    log_info "Extracting SCAN dataset..."
    unzip -q -o "${SCAN_ZIP}" -d "${DATA_DIR}/scan_tmp"
    
    # Locate the extracted files
    SCAN_EXTRACTED="${DATA_DIR}/scan_tmp/SCAN-master"
    
    # ── Simple split ────────────────────────────────────────────────
    if [ -f "${SCAN_EXTRACTED}/simple_split/tasks_train_simple.txt" ]; then
        cp "${SCAN_EXTRACTED}/simple_split/tasks_train_simple.txt" "${SCAN_DIR}/simple/train.txt"
        cp "${SCAN_EXTRACTED}/simple_split/tasks_test_simple.txt" "${SCAN_DIR}/simple/test.txt"
        log_ok "Simple split ready."
    fi
    
    # ── Length split ────────────────────────────────────────────────
    if [ -f "${SCAN_EXTRACTED}/length_split/tasks_train_length.txt" ]; then
        cp "${SCAN_EXTRACTED}/length_split/tasks_train_length.txt" "${SCAN_DIR}/length/train.txt"
        cp "${SCAN_EXTRACTED}/length_split/tasks_test_length.txt" "${SCAN_DIR}/length/test.txt"
        log_ok "Length split ready."
    fi
    
    # ── Add-jump split ──────────────────────────────────────────────
    if [ -f "${SCAN_EXTRACTED}/add_prim_split/tasks_train_addprim_jump.txt" ]; then
        cp "${SCAN_EXTRACTED}/add_prim_split/tasks_train_addprim_jump.txt" "${SCAN_DIR}/addprim_jump/train.txt"
        cp "${SCAN_EXTRACTED}/add_prim_split/tasks_test_addprim_jump.txt" "${SCAN_DIR}/addprim_jump/test.txt"
        log_ok "Add-jump split ready."
    fi
    
    # ── Cleanup ─────────────────────────────────────────────────────
    rm -rf "${DATA_DIR}/scan_tmp" "${SCAN_ZIP}"
    log_ok "Cleanup complete."
fi

# ── Print dataset statistics ────────────────────────────────────────
echo ""
log_info "Dataset statistics:"
for split in simple length addprim_jump; do
    TRAIN_FILE="${SCAN_DIR}/${split}/train.txt"
    TEST_FILE="${SCAN_DIR}/${split}/test.txt"
    if [ -f "$TRAIN_FILE" ] && [ -f "$TEST_FILE" ]; then
        TRAIN_LINES=$(wc -l < "$TRAIN_FILE")
        TEST_LINES=$(wc -l < "$TEST_FILE")
        echo "  ${split}: train=${TRAIN_LINES} test=${TEST_LINES}"
    else
        echo "  ${split}: NOT FOUND"
    fi
done

echo ""
log_ok "All training data ready."
