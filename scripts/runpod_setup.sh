#!/bin/bash
# ============================================================================
# ACRE H100 Training Environment Setup for RunPod
# ============================================================================
# Usage:
#   bash scripts/runpod_setup.sh
#
# This script sets up a fresh RunPod H100 instance for ACRE training.
# It installs all system and Python dependencies, downloads training data,
# verifies GPU access, and prints the command to start training.
#
# Prerequisites:
#   - RunPod instance with NVIDIA H100 GPU (80 GB HBM3)
#   - Ubuntu 22.04+ base image with CUDA 12.1+
#   - Internet access for package downloads
# ============================================================================

set -euo pipefail

# ── Colors for output ───────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo "============================================="
echo "  ACRE H100 Training Environment Setup"
echo "  Algebraic Concept Reasoning Engine"
echo "============================================="
echo ""

# ── Step 1: System Dependencies ─────────────────────────────────────
log_info "Installing system dependencies..."
apt-get update -qq && apt-get install -y -qq \
    git wget curl unzip \
    build-essential \
    libffi-dev libssl-dev \
    > /dev/null 2>&1
log_ok "System dependencies installed."

# ── Step 2: Python Environment ──────────────────────────────────────
log_info "Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]); then
    log_error "Python 3.10+ required, found $PYTHON_VERSION"
    exit 1
fi
log_ok "Python $PYTHON_VERSION detected."

# ── Step 3: PyTorch with CUDA 12.1 ──────────────────────────────────
log_info "Installing PyTorch with CUDA 12.1 support..."
pip install --quiet --upgrade pip setuptools wheel
pip install --quiet torch torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu121
log_ok "PyTorch installed."

# ── Step 4: ACRE Package ────────────────────────────────────────────
log_info "Installing ACRE and development dependencies..."
if [ -f "pyproject.toml" ]; then
    pip install --quiet -e '.[dev]'
    log_ok "ACRE installed in editable mode with dev extras."
else
    log_warn "pyproject.toml not found. Installing from requirements.txt..."
    pip install --quiet -r requirements.txt
    log_ok "Dependencies from requirements.txt installed."
fi

# ── Step 5: Download Training Data ──────────────────────────────────
log_info "Downloading training data..."
if [ -f "scripts/download_training_data.sh" ]; then
    bash scripts/download_training_data.sh
    log_ok "Training data downloaded."
else
    log_warn "download_training_data.sh not found. Skipping data download."
    log_warn "You can manually download SCAN data to data/scan/"
fi

# ── Step 6: Verify GPU ──────────────────────────────────────────────
log_info "Verifying GPU setup..."
echo ""

GPU_CHECK=$(python3 -c "
import torch
import sys

if not torch.cuda.is_available():
    print('ERROR: CUDA is not available!')
    sys.exit(1)

gpu_name = torch.cuda.get_device_name(0)
gpu_mem_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
cuda_version = torch.version.cuda
torch_version = torch.__version__
n_gpus = torch.cuda.device_count()

# Check bf16 support (critical for H100)
bf16_support = torch.cuda.is_bf16_supported()

print(f'  PyTorch:       {torch_version}')
print(f'  CUDA:          {cuda_version}')
print(f'  GPU(s):        {n_gpus}x {gpu_name}')
print(f'  Memory:        {gpu_mem_gb:.1f} GB')
print(f'  BF16 support:  {bf16_support}')
print(f'  Compile ready: {hasattr(torch, \"compile\")}')
" 2>&1) || {
    log_error "GPU verification failed!"
    echo "$GPU_CHECK"
    exit 1
}

echo "$GPU_CHECK"
echo ""
log_ok "GPU setup verified."

# ── Step 7: Verify Concept Library ──────────────────────────────────
log_info "Checking concept library..."
if [ -f "data/concept_library/seed_concepts.json" ]; then
    N_CONCEPTS=$(python3 -c "
import json
with open('data/concept_library/seed_concepts.json') as f:
    data = json.load(f)
print(len(data.get('concepts', [])))
")
    log_ok "Seed concept library found: $N_CONCEPTS concepts."
else
    log_warn "Seed concept library not found at data/concept_library/seed_concepts.json"
fi

# ── Step 8: Run Quick Sanity Test ────────────────────────────────────
log_info "Running quick sanity tests..."
if python3 -m pytest tests/ -x -q --tb=line 2>/dev/null; then
    log_ok "All sanity tests passed."
else
    log_warn "Some tests failed. This may be expected before training."
fi

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo "============================================="
echo "  Setup Complete!"
echo "============================================="
echo ""
echo "  To start training:"
echo "    python -m acre.training.train --config configs/scan_h100.yaml"
echo ""
echo "  To run full test suite:"
echo "    pytest tests/ -v --cov=src/acre"
echo ""
echo "  To monitor with TensorBoard:"
echo "    tensorboard --logdir results/h100_training/tensorboard"
echo ""
echo "============================================="
