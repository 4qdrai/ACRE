"""
Contrastive Pretraining — InfoNCE learning for concept and problem embeddings.

The idea: concepts that describe the *same* thing (even with different wording
or slight noise) should land close together in embedding space, while
genuinely *different* concepts should be far apart.  This is like teaching
the model to recognise that "gradient descent" and "steepest descent method"
are neighbours, while "gradient descent" and "plate tectonics" are not.

Training signal is entirely **self-supervised**: we generate positive pairs
by augmenting concept tensors (dropout elements, add noise) and mine hard
negatives from the batch.

Classes:
    ConceptAugmenter: Data augmentation for ConceptTensor elements.
    InfoNCELoss: Temperature-scaled InfoNCE contrastive loss.
    ConceptContrastiveTrainer: Full training loop with mixed-precision.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import DataLoader, Dataset

# Version-agnostic imports for amp
try:
    from torch.amp import autocast, GradScaler
except ImportError:
    from torch.cuda.amp import autocast, GradScaler

logger = logging.getLogger(__name__)

NUM_ELEMENTS = 10
ELEMENT_DIM = 64


# ---------------------------------------------------------------------------
# Data augmentation
# ---------------------------------------------------------------------------

class ConceptAugmenter:
    """Augments concept tensors to create positive pairs for contrastive learning.

    Three augmentation strategies:
        1. Element dropout — randomly zero out entire element slots
        2. Gaussian noise — small perturbation to element values
        3. Element shuffle — swap two random element positions (mild)
    """

    def __init__(
        self,
        dropout_prob: float = 0.15,
        noise_std: float = 0.05,
        shuffle_prob: float = 0.1,
    ) -> None:
        self.dropout_prob = dropout_prob
        self.noise_std = noise_std
        self.shuffle_prob = shuffle_prob

    def __call__(self, x: Tensor) -> Tensor:
        """Augment a (10, d) concept tensor → new (10, d) tensor."""
        aug = x.clone()

        # 1. Element dropout
        mask = torch.bernoulli(torch.full((x.shape[0], 1), 1 - self.dropout_prob))
        aug = aug * mask.to(aug.device)

        # 2. Gaussian noise
        aug = aug + torch.randn_like(aug) * self.noise_std

        # 3. Element shuffle
        if torch.rand(1).item() < self.shuffle_prob:
            i, j = torch.randperm(x.shape[0])[:2].tolist()
            aug[i], aug[j] = aug[j].clone(), aug[i].clone()

        return aug


# ---------------------------------------------------------------------------
# InfoNCE loss
# ---------------------------------------------------------------------------

class InfoNCELoss(nn.Module):
    """Temperature-scaled InfoNCE loss (symmetric).

    Given a batch of N (anchor, positive) pairs, the loss encourages
    anchor_i to be closest to positive_i while being far from all other
    positives_j (j ≠ i).  This is the same loss used in CLIP and SimCLR.
    """

    def __init__(self, temperature: float = 0.07) -> None:
        super().__init__()
        self.temperature = temperature

    def forward(self, anchors: Tensor, positives: Tensor, labels: Optional[Tensor] = None) -> Tensor:
        """
        Args:
            anchors:   (N, D) L2-normalised embeddings.
            positives: (N, D) L2-normalised embeddings.
            labels:    (N,) optional integer labels. When provided, same-label
                       off-diagonal pairs are masked to prevent false-negative
                       collisions (Khosla et al., 2020).

        Returns:
            Scalar loss.
        """
        anchors = F.normalize(anchors, dim=-1)
        positives = F.normalize(positives, dim=-1)

        # Cosine similarity matrix (N × N)
        logits = anchors @ positives.T / self.temperature

        # Supervised masking: neutralize false negatives from same-cluster samples
        if labels is not None:
            label_match = labels.unsqueeze(0) == labels.unsqueeze(1)
            # Keep the diagonal (true positives) visible
            label_match.fill_diagonal_(False)
            # Set same-label off-diagonal logits to -inf so they don't
            # contribute to the denominator as negatives
            logits = logits.masked_fill(label_match, -float("inf"))

        targets = torch.arange(logits.size(0), device=logits.device)

        # Symmetric loss
        loss_a2p = F.cross_entropy(logits, targets)
        loss_p2a = F.cross_entropy(logits.T, targets)
        return (loss_a2p + loss_p2a) / 2


# ---------------------------------------------------------------------------
# Projection head
# ---------------------------------------------------------------------------

class ProjectionHead(nn.Module):
    """MLP that maps concept tensors (10×d flattened) to a contrastive space."""

    def __init__(self, input_dim: int = NUM_ELEMENTS * ELEMENT_DIM, proj_dim: int = 256) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, input_dim),
            nn.GELU(),
            nn.LayerNorm(input_dim),
            nn.Linear(input_dim, proj_dim),
        )

    def forward(self, x: Tensor) -> Tensor:
        """x: (batch, 10, d) → (batch, proj_dim)."""
        flat = x.reshape(x.size(0), -1)
        return self.net(flat)


# ---------------------------------------------------------------------------
# Synthetic concept dataset (for standalone testing)
# ---------------------------------------------------------------------------

class SyntheticConceptDataset(Dataset):
    """Generates random concept tensors for pipeline testing."""

    def __init__(self, size: int = 10_000, n_elements: int = NUM_ELEMENTS, element_dim: int = ELEMENT_DIM) -> None:
        self.data = torch.randn(size, n_elements, element_dim)
        # Assign cluster labels (simulate semantic groups)
        self.labels = torch.randint(0, 50, (size,))

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx: int) -> Tuple[Tensor, int]:
        return self.data[idx], self.labels[idx].item()


# ---------------------------------------------------------------------------
# Hard negative mining
# ---------------------------------------------------------------------------

def mine_hard_negatives(
    embeddings: Tensor,
    labels: Tensor,
    top_k: int = 5,
) -> Tensor:
    """Find the hardest negatives — closest embeddings with different labels.

    Args:
        embeddings: (N, D) normalised embeddings.
        labels: (N,) integer labels.
        top_k: Number of hard negatives to consider.

    Returns:
        indices: (N,) index of the hardest negative for each sample.
    """
    sim = embeddings @ embeddings.T  # (N, N)
    # Mask out same-label pairs by setting similarity to -inf
    label_match = labels.unsqueeze(0) == labels.unsqueeze(1)
    sim[label_match] = -float("inf")
    # Hardest negative = highest similarity among different-label samples
    hard_neg_indices = sim.argmax(dim=1)
    return hard_neg_indices


# ---------------------------------------------------------------------------
# Cross-modal pairing (concept ↔ problem)
# ---------------------------------------------------------------------------

class CrossModalProjection(nn.Module):
    """Projects concepts and problems into a shared contrastive space."""

    def __init__(self, input_dim: int = NUM_ELEMENTS * ELEMENT_DIM, shared_dim: int = 256) -> None:
        super().__init__()
        self.concept_proj = ProjectionHead(input_dim, shared_dim)
        self.problem_proj = ProjectionHead(input_dim, shared_dim)

    def forward(
        self,
        concepts: Tensor,
        problems: Tensor,
    ) -> Tuple[Tensor, Tensor]:
        """
        Args:
            concepts: (B, 10, d) concept tensors.
            problems: (B, 10, d) problem tensors.

        Returns:
            Tuple of (concept_embeds, problem_embeds) both (B, shared_dim).
        """
        return self.concept_proj(concepts), self.problem_proj(problems)


# ---------------------------------------------------------------------------
# Main trainer
# ---------------------------------------------------------------------------

@dataclass
class TrainingConfig:
    """Hyperparameters for contrastive pretraining."""
    epochs: int = 50
    batch_size: int = 256
    lr: float = 3e-4
    weight_decay: float = 0.01
    temperature: float = 0.07
    warmup_steps: int = 500
    gradient_accumulation_steps: int = 4
    use_mixed_precision: bool = True
    log_every: int = 50
    checkpoint_dir: str = "checkpoints/contrastive"
    device: str = "cuda" if torch.cuda.is_available() else "cpu"


class ConceptContrastiveTrainer:
    """Full contrastive pretraining loop for concept embeddings.

    This trainer:
        1. Augments each concept tensor twice to form a positive pair
        2. Uses InfoNCE loss to push positive pairs together / negatives apart
        3. Optionally mines hard negatives for faster convergence
        4. Supports mixed-precision (bf16) and gradient accumulation
        5. Logs to TensorBoard

    Usage::

        trainer = ConceptContrastiveTrainer(config)
        trainer.train(dataset)
        trainer.save_checkpoint("epoch_50.pt")
    """

    def __init__(self, config: Optional[TrainingConfig] = None) -> None:
        self.config = config or TrainingConfig()
        self.device = torch.device(self.config.device)

        # Models
        self.projection = ProjectionHead().to(self.device)
        self.criterion = InfoNCELoss(temperature=self.config.temperature)
        self.augmenter = ConceptAugmenter()

        # Optimiser
        self.optimizer = AdamW(
            self.projection.parameters(),
            lr=self.config.lr,
            weight_decay=self.config.weight_decay,
        )
        
        # Setup scaler robustly
        if self.device.type == "cuda":
            try:
                self.scaler = GradScaler("cuda", enabled=self.config.use_mixed_precision)
            except (TypeError, ImportError):
                self.scaler = GradScaler(enabled=self.config.use_mixed_precision)
        else:
            self.scaler = GradScaler(enabled=False)

        # Logging
        self.history: List[Dict[str, float]] = []

    def _augment_batch(self, batch: Tensor) -> Tuple[Tensor, Tensor]:
        """Create two augmented views of every sample in the batch."""
        view_a = torch.stack([self.augmenter(x) for x in batch])
        view_b = torch.stack([self.augmenter(x) for x in batch])
        return view_a.to(self.device), view_b.to(self.device)

    def train(self, dataset: Dataset, val_dataset: Optional[Dataset] = None) -> List[Dict[str, float]]:
        """Run the full contrastive training loop.

        Args:
            dataset: A dataset yielding (concept_tensor, label) tuples.
            val_dataset: Optional validation set.

        Returns:
            Training history as list of dicts.
        """
        loader = DataLoader(
            dataset, batch_size=self.config.batch_size,
            shuffle=True, drop_last=True, num_workers=0,
        )

        steps_per_epoch = math.ceil(len(loader) / self.config.gradient_accumulation_steps)
        scheduler = CosineAnnealingLR(
            self.optimizer,
            T_max=self.config.epochs * steps_per_epoch,
            eta_min=1e-6,
        )

        self.projection.train()
        global_step = 0

        for epoch in range(1, self.config.epochs + 1):
            epoch_loss = 0.0
            n_batches = 0

            for batch_idx, (concepts, labels) in enumerate(loader):
                # Create positive pairs via augmentation
                view_a, view_b = self._augment_batch(concepts)

                if self.device.type == "cuda":
                    ctx = autocast(device_type="cuda", enabled=self.config.use_mixed_precision)
                else:
                    from contextlib import nullcontext
                    ctx = nullcontext()

                with ctx:
                    z_a = self.projection(view_a)
                    z_b = self.projection(view_b)
                    loss = self.criterion(z_a, z_b, labels=labels.to(self.device))
                    loss = loss / self.config.gradient_accumulation_steps

                self.scaler.scale(loss).backward()

                is_accum_step = (batch_idx + 1) % self.config.gradient_accumulation_steps == 0
                is_last_step = (batch_idx + 1) == len(loader)

                if is_accum_step or is_last_step:
                    scale_before = self.scaler.get_scale()
                    self.scaler.step(self.optimizer)
                    self.scaler.update()
                    self.optimizer.zero_grad()
                    # Only step scheduler if the optimizer actually stepped
                    # (GradScaler skips the step when NaN gradients are detected)
                    if scale_before <= self.scaler.get_scale():
                        scheduler.step()

                epoch_loss += loss.item() * self.config.gradient_accumulation_steps
                n_batches += 1
                global_step += 1

                if global_step % self.config.log_every == 0:
                    avg = epoch_loss / n_batches
                    lr_now = scheduler.get_last_lr()[0]
                    logger.info(f"step {global_step}  loss={avg:.4f}  lr={lr_now:.2e}")

            avg_epoch_loss = epoch_loss / max(n_batches, 1)
            record = {"epoch": epoch, "train_loss": avg_epoch_loss, "lr": scheduler.get_last_lr()[0]}

            # Validation
            if val_dataset is not None:
                val_loss = self._validate(val_dataset)
                record["val_loss"] = val_loss

            self.history.append(record)
            print(f"Epoch {epoch}/{self.config.epochs}  loss={avg_epoch_loss:.4f}")

        return self.history

    @torch.no_grad()
    def _validate(self, dataset: Dataset) -> float:
        """Compute validation loss."""
        loader = DataLoader(dataset, batch_size=self.config.batch_size, shuffle=False)
        self.projection.eval()
        total_loss = 0.0
        n = 0
        for concepts, _ in loader:
            view_a, view_b = self._augment_batch(concepts)
            z_a = self.projection(view_a)
            z_b = self.projection(view_b)
            total_loss += self.criterion(z_a, z_b).item()
            n += 1
        self.projection.train()
        return total_loss / max(n, 1)

    def save_checkpoint(self, path: str) -> None:
        """Save model checkpoint."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        torch.save({
            "projection": self.projection.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "config": self.config,
            "history": self.history,
        }, path)
        print(f"Checkpoint saved -> {path}")

    def load_checkpoint(self, path: str) -> None:
        """Load model checkpoint."""
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.projection.load_state_dict(ckpt["projection"])
        self.optimizer.load_state_dict(ckpt["optimizer"])
        self.history = ckpt.get("history", [])
        print(f"Checkpoint loaded <- {path}")


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("ACRE — Contrastive Pretraining Demo")
    print("=" * 60)

    config = TrainingConfig(epochs=3, batch_size=64, log_every=10)
    trainer = ConceptContrastiveTrainer(config)

    dataset = SyntheticConceptDataset(size=2_000)
    history = trainer.train(dataset)

    print("\nTraining history:")
    for rec in history:
        print(f"  Epoch {rec['epoch']}: loss={rec['train_loss']:.4f}")

    print("\nDone [OK]")
