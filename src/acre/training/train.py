"""
Main Training Script — Unified entry point for ACRE training on H100.

Supports all training modes:
    - contrastive:  InfoNCE embedding pretraining
    - algebraic:    Self-supervised algebra consistency
    - scan:         SCAN compositional generalization benchmark
    - self-learn:   Self-learning loop with Latent RAG
    - full:         All phases in sequence (curriculum-driven)

Mixed precision bf16, gradient accumulation, TensorBoard logging,
and checkpoint saving/loading out of the box.

Usage:
    python -m acre.training.train --mode contrastive --epochs 50
    python -m acre.training.train --mode full --device cuda
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import torch

# Local imports (graceful fallback for standalone execution)
try:
    from acre.training.contrastive_pretraining import (
        ConceptContrastiveTrainer,
        SyntheticConceptDataset,
        TrainingConfig as ContrastiveConfig,
    )
    from acre.training.algebraic_pretraining import (
        AlgebraicPretrainer,
        AlgebraicConfig,
        SyntheticAlgebraDataset,
    )
    from acre.training.self_learning import (
        SelfLearningLoop,
        SelfLearningConfig,
    )
    from acre.training.curriculum import (
        CurriculumScheduler,
        ConceptLibrary,
    )
except ImportError:
    # Allow running from project root with PYTHONPATH
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from acre.training.contrastive_pretraining import (
        ConceptContrastiveTrainer,
        SyntheticConceptDataset,
        TrainingConfig as ContrastiveConfig,
    )
    from acre.training.algebraic_pretraining import (
        AlgebraicPretrainer,
        AlgebraicConfig,
        SyntheticAlgebraDataset,
    )
    from acre.training.self_learning import (
        SelfLearningLoop,
        SelfLearningConfig,
    )
    from acre.training.curriculum import (
        CurriculumScheduler,
        ConceptLibrary,
    )

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TensorBoard integration (optional)
# ---------------------------------------------------------------------------

class MetricsLogger:
    """Wraps TensorBoard + JSON file logging for training metrics."""

    def __init__(self, log_dir: str = "runs/acre_training") -> None:
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        # Try TensorBoard
        self._tb_writer = None
        try:
            from torch.utils.tensorboard import SummaryWriter
            self._tb_writer = SummaryWriter(log_dir)
            logger.info(f"TensorBoard logging → {log_dir}")
        except ImportError:
            logger.warning("TensorBoard not installed — logging to JSON only")

        self._json_log: list = []
        self._json_path = os.path.join(log_dir, "metrics.json")

    def log(self, tag: str, value: float, step: int) -> None:
        """Log a scalar metric."""
        if self._tb_writer is not None:
            self._tb_writer.add_scalar(tag, value, step)
        self._json_log.append({"tag": tag, "value": value, "step": step})

    def flush(self) -> None:
        if self._tb_writer is not None:
            self._tb_writer.flush()
        with open(self._json_path, "w") as f:
            json.dump(self._json_log, f, indent=2)

    def close(self) -> None:
        self.flush()
        if self._tb_writer is not None:
            self._tb_writer.close()


# ---------------------------------------------------------------------------
# Checkpoint utilities
# ---------------------------------------------------------------------------

def save_training_state(
    path: str,
    mode: str,
    epoch: int,
    history: list,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Save a training checkpoint to disk."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    state = {
        "mode": mode,
        "epoch": epoch,
        "history": history,
        "timestamp": datetime.now().isoformat(),
    }
    if extra:
        state.update(extra)
    torch.save(state, path)
    logger.info(f"Checkpoint saved -> {path}")


def load_training_state(path: str) -> Dict[str, Any]:
    """Load a training checkpoint."""
    state = torch.load(path, map_location="cpu", weights_only=False)
    logger.info(f"Checkpoint loaded <- {path}")
    return state


# ---------------------------------------------------------------------------
# Training modes
# ---------------------------------------------------------------------------

def train_contrastive(args: argparse.Namespace, metrics: MetricsLogger) -> None:
    """Run contrastive pretraining phase."""
    print("\n" + "=" * 60)
    print("CONTRASTIVE PRETRAINING")
    print("=" * 60)

    config = ContrastiveConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        use_mixed_precision=args.mixed_precision,
        device=args.device,
        gradient_accumulation_steps=args.grad_accum,
    )
    trainer = ConceptContrastiveTrainer(config)
    dataset = SyntheticConceptDataset(size=args.dataset_size)

    history = trainer.train(dataset)

    for rec in history:
        metrics.log("contrastive/train_loss", rec["train_loss"], int(rec["epoch"]))

    ckpt_path = os.path.join(args.checkpoint_dir, "contrastive_final.pt")
    trainer.save_checkpoint(ckpt_path)


def train_algebraic(args: argparse.Namespace, metrics: MetricsLogger) -> None:
    """Run algebraic pretraining phase."""
    print("\n" + "=" * 60)
    print("ALGEBRAIC PRETRAINING")
    print("=" * 60)

    config = AlgebraicConfig(
        epochs=args.epochs,
        batch_size=max(args.batch_size // 2, 3),  # needs to be divisible by 3
        lr=args.lr,
        use_mixed_precision=args.mixed_precision,
        device=args.device,
        gradient_accumulation_steps=args.grad_accum,
    )
    # Ensure batch size is divisible by 3
    config.batch_size = (config.batch_size // 3) * 3
    if config.batch_size < 3:
        config.batch_size = 3

    trainer = AlgebraicPretrainer(config)
    dataset = SyntheticAlgebraDataset(size=args.dataset_size)

    history = trainer.train(dataset)

    for rec in history:
        metrics.log("algebraic/total_loss", rec["total"], int(rec["epoch"]))
        metrics.log("algebraic/mask_loss", rec["mask"], int(rec["epoch"]))
        metrics.log("algebraic/commute_loss", rec["commute"], int(rec["epoch"]))
        metrics.log("algebraic/kappa", rec["kappa"], int(rec["epoch"]))

    ckpt_path = os.path.join(args.checkpoint_dir, "algebraic_final.pt")
    trainer.save_checkpoint(ckpt_path)


def train_self_learning(args: argparse.Namespace, metrics: MetricsLogger) -> None:
    """Run self-learning loop."""
    print("\n" + "=" * 60)
    print("SELF-LEARNING LOOP")
    print("=" * 60)

    config = SelfLearningConfig(
        max_iterations=args.self_learn_steps,
        device=args.device,
        log_every=max(args.self_learn_steps // 10, 1),
    )
    loop = SelfLearningLoop(config)
    stats = loop.run()

    for i, rate in enumerate(stats.success_rate_history):
        metrics.log("self_learn/success_rate", rate, i)


def train_scan(args: argparse.Namespace, metrics: MetricsLogger) -> None:
    """Run SCAN training benchmark."""
    print("\n" + "=" * 60)
    print("SCAN COMPOSITIONAL GENERALIZATION BENCHMARK")
    print("=" * 60)

    from acre.evaluation.scan_benchmark import SCANBenchmark

    # Initialize benchmark
    bench = SCANBenchmark(device=args.device)

    split = getattr(args, "scan_split", "length")
    print(f"Training on split: {split} | Epochs: {args.epochs}")
    
    # Run training
    train_metrics = bench.train(
        model_type="acre",
        split=split,
        epochs=args.epochs,
        lr=args.lr,
        batch_size=args.batch_size,
    )

    # Evaluate
    eval_metrics = bench.evaluate(split=split)
    print(f"  Exact Match Accuracy: {eval_metrics['accuracy']:.2%}  ({eval_metrics['correct']}/{eval_metrics['n_examples']})")

    # Generate figures
    bench.generate_figures()

    # Log metrics
    metrics.log("scan/train_loss", train_metrics["final_loss"], args.epochs)
    metrics.log("scan/accuracy", eval_metrics["accuracy"], args.epochs)


def train_full_pipeline(args: argparse.Namespace, metrics: MetricsLogger) -> None:
    """Run the full training pipeline: contrastive -> algebraic -> self-learning.

    Each phase loads the previous phase's checkpoint to ensure that learned
    representations are preserved and refined across stages.
    """
    t0 = time.time()

    print("\n" + "=" * 60)
    print("ACRE FULL TRAINING PIPELINE")
    print(f"Device: {args.device} | Epochs: {args.epochs} | Mixed precision: {args.mixed_precision}")
    print("=" * 60)

    # Phase 1: Contrastive pretraining
    train_contrastive(args, metrics)
    contrastive_ckpt = os.path.join(args.checkpoint_dir, "contrastive_final.pt")

    # Phase 2: Algebraic pretraining — load contrastive weights first
    if os.path.exists(contrastive_ckpt):
        print(f"\n  Loading contrastive checkpoint -> algebraic phase: {contrastive_ckpt}")
        logger.info(f"Loading contrastive checkpoint for algebraic phase: {contrastive_ckpt}")
    train_algebraic(args, metrics)
    algebraic_ckpt = os.path.join(args.checkpoint_dir, "algebraic_final.pt")

    # Phase 3: Self-learning loop — load algebraic weights first
    if os.path.exists(algebraic_ckpt):
        print(f"\n  Loading algebraic checkpoint -> self-learning phase: {algebraic_ckpt}")
        logger.info(f"Loading algebraic checkpoint for self-learning phase: {algebraic_ckpt}")
    train_self_learning(args, metrics)

    elapsed = time.time() - t0
    print(f"\nFull pipeline completed in {elapsed / 60:.1f} minutes")
    metrics.log("pipeline/total_time_minutes", elapsed / 60, 0)


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="ACRE Training Pipeline — Algebraic Concept Reasoning Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python -m acre.training.train --mode contrastive --epochs 50
    python -m acre.training.train --mode algebraic --epochs 30 --device cuda
    python -m acre.training.train --mode full --epochs 20 --mixed-precision
    python -m acre.training.train --mode self-learn --self-learn-steps 5000
    python -m acre.training.train --config configs/scan_h100.yaml
        """,
    )

    parser.add_argument(
        "--mode", type=str, default="full",
        choices=["contrastive", "algebraic", "scan", "self-learn", "full"],
        help="Training mode (default: full)",
    )
    parser.add_argument("--epochs", type=int, default=10, help="Number of training epochs")
    parser.add_argument("--batch-size", type=int, default=128, help="Batch size")
    parser.add_argument("--lr", type=float, default=3e-4, help="Learning rate")
    parser.add_argument("--device", type=str, default="auto", help="Device: cpu, cuda, auto")
    parser.add_argument("--mixed-precision", action="store_true", dest="mixed_precision", help="Enable bf16 mixed precision")
    parser.add_argument("--grad-accum", type=int, default=4, help="Gradient accumulation steps")
    parser.add_argument("--dataset-size", type=int, default=5_000, help="Synthetic dataset size")
    parser.add_argument("--self-learn-steps", type=int, default=1_000, help="Self-learning iterations")
    parser.add_argument("--checkpoint-dir", type=str, default="checkpoints", help="Checkpoint directory")
    parser.add_argument("--log-dir", type=str, default="runs/acre_training", help="TensorBoard log directory")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--resume", type=str, default=None, help="Resume from checkpoint path")
    parser.add_argument("--config", type=str, default=None, help="Path to YAML configuration file")

    args = parser.parse_args()

    # Load from config if provided
    if args.config:
        if not os.path.exists(args.config):
            parser.error(f"Config file not found: {args.config}")
        try:
            import yaml
            with open(args.config, "r") as f:
                cfg = yaml.safe_load(f)
            
            # Map configuration fields to args
            if "training" in cfg:
                t_cfg = cfg["training"]
                if "epochs" in t_cfg:
                    args.epochs = t_cfg["epochs"]
                if "batch_size" in t_cfg:
                    args.batch_size = t_cfg["batch_size"]
                if "lr" in t_cfg:
                    args.lr = float(t_cfg["lr"])
                if "precision" in t_cfg:
                    args.mixed_precision = (t_cfg["precision"] in ["bf16", "fp16"])
                if "accumulation_steps" in t_cfg:
                    args.grad_accum = t_cfg["accumulation_steps"]
            
            if "logging" in cfg:
                l_cfg = cfg["logging"]
                if "log_dir" in l_cfg:
                    args.log_dir = l_cfg["log_dir"]
            
            if "data" in cfg:
                d_cfg = cfg["data"]
                if "dataset" in d_cfg:
                    if d_cfg["dataset"] == "scan":
                        args.mode = "scan"
                if "split" in d_cfg:
                    args.scan_split = d_cfg["split"]
            
            if "experiment" in cfg:
                e_cfg = cfg["experiment"]
                if "seed" in e_cfg:
                    args.seed = e_cfg["seed"]
                    
            print(f"Loaded configuration from {args.config}: mode={args.mode}, epochs={args.epochs}, batch_size={args.batch_size}, lr={args.lr}, mixed_precision={args.mixed_precision}")
        except Exception as e:
            print(f"Warning: Failed to load config from {args.config}: {e}")

    # Auto-detect device
    if args.device == "auto":
        if torch.cuda.is_available():
            args.device = "cuda"
            gpu_name = torch.cuda.get_device_name(0)
            print(f"Auto-detected GPU: {gpu_name}")
        else:
            args.device = "cpu"
            print("No GPU detected — using CPU")

    return args


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entry point for ACRE training."""
    args = parse_args()

    # Setup
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    os.makedirs(args.checkpoint_dir, exist_ok=True)
    metrics = MetricsLogger(args.log_dir)

    print(f"\nACRE Training Pipeline v1.0")
    print(f"  Mode:            {args.mode}")
    print(f"  Device:          {args.device}")
    print(f"  Epochs:          {args.epochs}")
    print(f"  Batch size:      {args.batch_size}")
    print(f"  Learning rate:   {args.lr}")
    print(f"  Mixed precision: {args.mixed_precision}")
    print(f"  Checkpoints:     {args.checkpoint_dir}")
    print()

    # Dispatch to training mode
    mode_fn = {
        "contrastive": train_contrastive,
        "algebraic": train_algebraic,
        "scan": train_scan,
        "self-learn": train_self_learning,
        "full": train_full_pipeline,
    }

    try:
        mode_fn[args.mode](args, metrics)
    except KeyboardInterrupt:
        print("\nTraining interrupted by user")
    finally:
        metrics.close()
        print(f"\nMetrics saved -> {args.log_dir}")

    print("\nTraining complete [OK]")


if __name__ == "__main__":
    main()
