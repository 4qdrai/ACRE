"""
Algebraic Pretraining — Self-supervised training using algebraic consistency.

The core insight: we don't need *labels* to train the algebraic engine.
The algebra itself provides the supervision signal!  If the model truly
understands concept composition, then combining concept A with concept B
should give the same result regardless of order (commutativity), and
iterative refinement should converge to a stable fixed point (Banach).

Four loss components:
    1. Mask-and-predict:    mask random elements, reconstruct from context
    2. Commutativity:       (A ⊕ B) ⊗ P ≈ (B ⊕ A) ⊗ P
    3. Fixed-point:         ||f(c) - c|| -> 0  (RSRA-4B convergence)
    4. Constraint (Φ mask): output must satisfy structural constraints
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor
from torch.nn.utils import spectral_norm
from torch.optim import AdamW
from torch.optim.lr_scheduler import OneCycleLR
from torch.utils.data import DataLoader, Dataset

from acre.core.algebra import ConceptAlgebra
from acre.core.constraint_mask import ConstraintMask

# Version-agnostic imports for amp
try:
    from torch.amp import autocast, GradScaler
except ImportError:
    from torch.cuda.amp import autocast, GradScaler

logger = logging.getLogger(__name__)

NUM_ELEMENTS = 10
ELEMENT_DIM = 64
TOTAL_DIM = NUM_ELEMENTS * ELEMENT_DIM


# ---------------------------------------------------------------------------
# Mask-and-predict module
# ---------------------------------------------------------------------------

class ElementPredictor(nn.Module):
    """Predicts masked elements from the remaining visible ones."""

    def __init__(self, n_elements: int = NUM_ELEMENTS, element_dim: int = ELEMENT_DIM) -> None:
        super().__init__()
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(
                d_model=element_dim, nhead=4, dim_feedforward=element_dim * 4,
                dropout=0.1, activation="gelu", batch_first=True,
            ),
            num_layers=2,
        )
        self.predictor = nn.Linear(element_dim, element_dim)

    def forward(self, elements: Tensor, mask: Tensor) -> Tensor:
        """
        Args:
            elements: (B, 10, d) with some elements zeroed.
            mask: (B, 10) bool — True where element is masked.

        Returns:
            predictions: (B, 10, d) predicted values for ALL elements.
        """
        encoded = self.encoder(elements)
        return self.predictor(encoded)


# ---------------------------------------------------------------------------
# LARE refinement operator (for fixed-point loss)
# ---------------------------------------------------------------------------

class LARERefiner(nn.Module):
    """Single refinement step matching the real LARE state_refiner architecture.

    Uses spectral normalization and Tanh activations to mirror the production
    LARE module, ensuring that fixed-point pretraining directly improves
    the weights used during actual reasoning.
    """

    def __init__(self, element_dim: int = ELEMENT_DIM) -> None:
        super().__init__()
        total_dim = NUM_ELEMENTS * element_dim
        self.refine = nn.Sequential(
            spectral_norm(nn.Linear(total_dim, total_dim)),
            nn.Tanh(),  # Strictly 1-Lipschitz for Banach contraction guarantee
            spectral_norm(nn.Linear(total_dim, total_dim)),
        )
        # Contraction factor — initialised < 1 for guaranteed convergence
        self.kappa = nn.Parameter(torch.tensor(0.5))

    def forward(self, c: Tensor) -> Tensor:
        """One refinement step: c -> T(c). Operates on (B, 10, d) -> (B, 10, d).
        
        Uses the Krasnoselskii-Mann averaged mapping:
            T(c) = (1 - κ) · c + κ · f(c)
        which is strictly contractive when κ ∈ (0, 1) and f is 1-Lipschitz.
        """
        orig_shape = c.shape
        flat = c.reshape(c.size(0), -1) if c.dim() == 3 else c.reshape(-1)
        residual = self.refine(flat)
        kappa_clamped = torch.sigmoid(self.kappa)  # Always in (0, 1)
        # Krasnoselskii-Mann averaged mapping: (1-κ)·x + κ·f(x)
        result = (1.0 - kappa_clamped) * flat + kappa_clamped * residual
        return result.reshape(orig_shape)


# ---------------------------------------------------------------------------
# Main trainer
# ---------------------------------------------------------------------------

@dataclass
class AlgebraicConfig:
    """Configuration for algebraic pretraining."""
    epochs: int = 30
    batch_size: int = 128
    lr: float = 1e-4
    weight_decay: float = 0.01
    mask_ratio: float = 0.3
    # Loss weights
    w_mask: float = 1.0
    w_commute: float = 0.5
    w_fixpoint: float = 0.3
    w_constraint: float = 0.2
    # Fixed-point iterations
    fp_steps: int = 5
    # Mixed precision
    use_mixed_precision: bool = True
    gradient_accumulation_steps: int = 2
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    checkpoint_dir: str = "checkpoints/algebraic"
    log_every: int = 20


class AlgebraicPretrainer:
    """Self-supervised algebraic consistency trainer.

    The model learns algebra *from the algebra itself*:

    - **Mask-and-predict** teaches element interdependence
    - **Commutativity loss** teaches that order shouldn't matter
    - **Fixed-point loss** teaches convergent reasoning
    - **Constraint loss** teaches to respect the Φ mask
    """

    def __init__(self, config: Optional[AlgebraicConfig] = None) -> None:
        self.cfg = config or AlgebraicConfig()
        self.device = torch.device(self.cfg.device)

        # Core modules
        self.algebra = ConceptAlgebra(d=ELEMENT_DIM).to(self.device)
        self.predictor = ElementPredictor(element_dim=ELEMENT_DIM).to(self.device)
        self.refiner = LARERefiner(element_dim=ELEMENT_DIM).to(self.device)
        self.phi_mask = ConstraintMask(d=ELEMENT_DIM).to(self.device)

        # Collect all parameters
        all_params = (
            list(self.algebra.parameters())
            + list(self.predictor.parameters())
            + list(self.refiner.parameters())
            + list(self.phi_mask.parameters())
        )

        self.optimizer = AdamW(all_params, lr=self.cfg.lr, weight_decay=self.cfg.weight_decay)
        
        # Setup scaler robustly
        if self.device.type == "cuda":
            try:
                self.scaler = GradScaler("cuda", enabled=self.cfg.use_mixed_precision)
            except (TypeError, ImportError):
                self.scaler = GradScaler(enabled=self.cfg.use_mixed_precision)
        else:
            self.scaler = GradScaler(enabled=False)
            
        self.history: List[Dict[str, float]] = []

    # ------------------------------------------------------------------
    # Loss computations
    # ------------------------------------------------------------------

    def _mask_predict_loss(self, batch: Tensor) -> Tensor:
        """Mask random elements and predict them from the rest."""
        B, N, D = batch.shape
        mask = torch.bernoulli(torch.full((B, N), self.cfg.mask_ratio)).bool().to(self.device)
        masked_input = batch.clone()
        masked_input[mask] = 0.0

        preds = self.predictor(masked_input, mask)
        # Loss only on masked positions
        loss = F.mse_loss(preds[mask], batch[mask])
        return loss

    def _commutativity_loss(self, a: Tensor, b: Tensor, p: Tensor) -> Tensor:
        """(A ⊕ B) ⊗ P should ≈ (B ⊕ A) ⊗ P."""
        ab = self.algebra.compose(a, b)
        ba = self.algebra.compose(b, a)
        # Using real ConceptAlgebra.bind(problem, concept)
        result_ab = self.algebra.bind(p, ab)
        result_ba = self.algebra.bind(p, ba)
        return F.mse_loss(result_ab, result_ba)

    def _fixed_point_loss(self, c: Tensor) -> Tensor:
        """Iterate T and measure convergence: ||T^t(c) - T^{t-1}(c)|| -> 0."""
        state = c
        total_diff = torch.tensor(0.0, device=self.device)
        for _ in range(self.cfg.fp_steps):
            new_state = self.refiner(state)
            total_diff = total_diff + F.mse_loss(new_state, state)
            state = new_state
        return total_diff / self.cfg.fp_steps

    def _constraint_loss(self, solution: Tensor, problem: Tensor, concept: Tensor) -> Tensor:
        """Solutions should pass through Φ mask without large distortion."""
        constraints = problem[:, 5, :]   # element 6 (0-indexed: 5)
        limitations = concept[:, 8, :]   # element 9 (0-indexed: 8)
        # Apply real ConstraintMask
        phi = self.phi_mask(constraints, limitations)  # (B, d)
        masked_sol = solution * phi.unsqueeze(1)
        # Good solutions are minimally affected by the mask
        return F.mse_loss(masked_sol, solution)

    # ------------------------------------------------------------------
    # Training loop
    # ------------------------------------------------------------------

    def train(self, dataset: Dataset) -> List[Dict[str, float]]:
        """Run the full algebraic pretraining loop."""
        loader = DataLoader(
            dataset, batch_size=self.cfg.batch_size,
            shuffle=True, drop_last=True, num_workers=0,
        )

        scheduler = OneCycleLR(
            self.optimizer,
            max_lr=self.cfg.lr,
            epochs=self.cfg.epochs,
            steps_per_epoch=len(loader),
        )

        global_step = 0

        for epoch in range(1, self.cfg.epochs + 1):
            losses = {"mask": 0.0, "commute": 0.0, "fixpoint": 0.0, "constraint": 0.0, "total": 0.0}
            n = 0

            for batch_idx, data in enumerate(loader):
                if isinstance(data, (list, tuple)):
                    data = data[0]
                batch = data.to(self.device)

                # Split batch into triplets (A, B, P) by thirds
                third = batch.size(0) // 3
                a, b, p = batch[:third], batch[third:2*third], batch[2*third:3*third]

                if self.device.type == "cuda":
                    ctx = autocast(device_type="cuda", enabled=self.cfg.use_mixed_precision)
                else:
                    from contextlib import nullcontext
                    ctx = nullcontext()

                with ctx:
                    l_mask = self._mask_predict_loss(batch)
                    l_comm = self._commutativity_loss(a, b, p)
                    l_fp = self._fixed_point_loss(a)

                    # Compose a and b, bind problem p, and project to solution
                    ab = self.algebra.compose(a, b)
                    bound = self.algebra.bind(p, ab)
                    sol = self.algebra.project_to_solution(bound, p)

                    l_con = self._constraint_loss(sol, p, a)

                    total = (
                        self.cfg.w_mask * l_mask
                        + self.cfg.w_commute * l_comm
                        + self.cfg.w_fixpoint * l_fp
                        + self.cfg.w_constraint * l_con
                    ) / self.cfg.gradient_accumulation_steps

                self.scaler.scale(total).backward()

                if (batch_idx + 1) % self.cfg.gradient_accumulation_steps == 0:
                    scale_before = self.scaler.get_scale()
                    self.scaler.step(self.optimizer)
                    self.scaler.update()
                    self.optimizer.zero_grad()
                    # Only step scheduler if the optimizer actually stepped
                    # (GradScaler skips the step when NaN gradients are detected)
                    if scale_before <= self.scaler.get_scale():
                        scheduler.step()

                factor = self.cfg.gradient_accumulation_steps
                losses["mask"] += l_mask.item()
                losses["commute"] += l_comm.item()
                losses["fixpoint"] += l_fp.item()
                losses["constraint"] += l_con.item()
                losses["total"] += total.item() * factor
                n += 1
                global_step += 1

                if global_step % self.cfg.log_every == 0:
                    avg_t = losses["total"] / n
                    logger.info(f"step {global_step}  total={avg_t:.4f}")

            record = {k: v / max(n, 1) for k, v in losses.items()}
            record["epoch"] = epoch
            record["kappa"] = torch.sigmoid(self.refiner.kappa).item()
            self.history.append(record)

            print(
                f"Epoch {epoch}/{self.cfg.epochs}  "
                f"total={record['total']:.4f}  mask={record['mask']:.4f}  "
                f"comm={record['commute']:.4f}  fp={record['fixpoint']:.4f}  "
                f"kappa={record['kappa']:.3f}"
            )

        return self.history

    def save_checkpoint(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        torch.save({
            "algebra": self.algebra.state_dict(),
            "predictor": self.predictor.state_dict(),
            "refiner": self.refiner.state_dict(),
            "phi_mask": self.phi_mask.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "history": self.history,
        }, path)
        print(f"Checkpoint saved -> {path}")


# ---------------------------------------------------------------------------
# Synthetic dataset
# ---------------------------------------------------------------------------

class SyntheticAlgebraDataset(Dataset):
    """Random concept tensors with algebraic structure for self-supervised training."""

    def __init__(self, size: int = 10000) -> None:
        self.data = torch.randn(size, NUM_ELEMENTS, ELEMENT_DIM) * 0.1

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx: int) -> Tensor:
        return self.data[idx]


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("ACRE — Algebraic Pretraining Demo")
    print("=" * 60)

    cfg = AlgebraicConfig(epochs=3, batch_size=63, log_every=5)  # 63 divisible by 3
    trainer = AlgebraicPretrainer(cfg)

    dataset = SyntheticAlgebraDataset(size=3000)
    history = trainer.train(dataset)

    print("\nTraining summary:")
    for rec in history:
        print(f"  Epoch {rec['epoch']}: total={rec['total']:.4f}  kappa={rec['kappa']:.3f}")

    print("\nDone [OK]")
