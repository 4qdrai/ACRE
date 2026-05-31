"""
Solution Decoder — Translational Decoder from Latent Space to Output
====================================================================

The **SolutionDecoder** is the final stage of the ACRE pipeline. It takes
a SolutionTensor (the algebraic result from LARE) and maps it back to
human-readable output: text tokens, code tokens, or formal specification
tokens.

Architecture: Non-Autoregressive with Iterative Refinement
-----------------------------------------------------------

Unlike standard autoregressive decoders (which generate one token at a time),
the SolutionDecoder predicts **all output positions in parallel**, then
*iteratively refines* the predictions. This is inspired by:

- **CMLM** (Ghazvininejad et al., 2019) — Mask-Predict
- **NAT** (Gu et al., 2018) — Non-Autoregressive Transformers

The process:

1. **Initial prediction**: A single forward pass predicts all ``max_length``
   output tokens simultaneously from the solution embedding.
2. **Iterative refinement**: The least confident positions are masked and
   re-predicted conditioned on the confident positions.
3. **Convergence**: Stops when predictions stabilize or max iterations hit.

.. code-block:: text

    SolutionTensor (10, d)
         │
         ▼  flatten + project
    [Position Embeddings]  ← learnable, length = max_length
         │
         ▼
    [Transformer Decoder]  ← cross-attends to solution embedding
         │
         ▼
    [Vocabulary Head]  ← projects to vocab logits
         │
         ▼
    Output tokens (max_length, vocab_size)

Iterative refinement loop:
    1. Predict all positions
    2. Identify low-confidence positions (below threshold)
    3. Mask those positions
    4. Re-predict masked positions conditioned on confident ones
    5. Repeat for K_refine iterations
"""

from __future__ import annotations

import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, Tuple

from acre.core.concept_tensor import NUM_CONCEPT_ELEMENTS, DEFAULT_EMBEDDING_DIM
from acre.core.solution_tensor import SolutionTensor


class SolutionDecoder(nn.Module):
    """Non-autoregressive decoder with iterative refinement.

    Decodes a SolutionTensor into output token logits in parallel,
    then refines low-confidence predictions through masked re-prediction.

    Parameters
    ----------
    d_solution : int
        Dimension of the solution tensor (10 * d by default).
    d_model : int
        Decoder hidden dimension (default 256).
    vocab_size : int
        Output vocabulary size (default 32000).
    num_layers : int
        Number of transformer decoder layers (default 4).
    num_heads : int
        Number of attention heads (default 4).
    dim_feedforward : int
        Feedforward dimension (default 512).
    max_output_len : int
        Maximum output sequence length (default 512).
    num_refine_steps : int
        Number of iterative refinement steps (default 3).
    mask_ratio_schedule : str
        How to schedule the mask ratio across refinement steps.
        'linear' (default): mask_ratio decreases linearly from 0.5 to 0.1.
        'cosine': follows a cosine schedule.
    dropout : float
        Dropout rate (default 0.1).

    Examples
    --------
    >>> decoder = SolutionDecoder(d_solution=640, vocab_size=1000, d_model=128)
    >>> solution = SolutionTensor.empty(d=64)
    >>> logits = decoder(solution, max_length=50)
    >>> logits.shape
    torch.Size([50, 1000])
    """

    def __init__(
        self,
        d_solution: int = NUM_CONCEPT_ELEMENTS * DEFAULT_EMBEDDING_DIM,
        d_model: int = 256,
        vocab_size: int = 32000,
        num_layers: int = 4,
        num_heads: int = 4,
        dim_feedforward: int = 512,
        max_output_len: int = 512,
        num_refine_steps: int = 3,
        mask_ratio_schedule: str = "linear",
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.d_solution = d_solution
        self.d_model = d_model
        self.vocab_size = vocab_size
        self.max_output_len = max_output_len
        self.num_refine_steps = num_refine_steps
        self.mask_ratio_schedule = mask_ratio_schedule

        # ── Solution projection ───────────────────────────────────
        # Maps the flat solution vector to the decoder's d_model space
        self.solution_proj = nn.Sequential(
            nn.Linear(d_solution, d_model),
            nn.GELU(),
            nn.LayerNorm(d_model),
        )

        # ── Learnable position embeddings ─────────────────────────
        self.position_embeddings = nn.Embedding(max_output_len, d_model)

        # ── Initial content predictor ─────────────────────────────
        # Predicts initial token embeddings from solution + position
        self.initial_predictor = nn.Sequential(
            nn.Linear(d_model + d_model, d_model),  # solution + position
            nn.GELU(),
            nn.LayerNorm(d_model),
        )

        # ── Length predictor ──────────────────────────────────────
        # Predicts the output length from the solution
        self.length_predictor = nn.Sequential(
            nn.Linear(d_solution, d_model),
            nn.GELU(),
            nn.Linear(d_model, max_output_len),
        )

        # ── Transformer decoder for refinement ────────────────────
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=d_model,
            nhead=num_heads,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.refine_decoder = nn.TransformerDecoder(
            decoder_layer, num_layers=num_layers
        )

        # ── Vocabulary projection head ────────────────────────────
        self.vocab_head = nn.Linear(d_model, vocab_size)

        # ── Confidence scorer ─────────────────────────────────────
        # Per-position confidence for deciding which positions to mask
        self.confidence_scorer = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Linear(d_model // 2, 1),
            nn.Sigmoid(),
        )

        # ── Mask token ────────────────────────────────────────────
        self.mask_embedding = nn.Parameter(torch.randn(d_model) * 0.02)

        self._init_weights()

    def _init_weights(self) -> None:
        """Xavier uniform initialization for linear layers."""
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Embedding):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def _get_mask_ratio(self, step: int) -> float:
        """Compute the mask ratio for a given refinement step.

        Returns a value in (0, 1) — the fraction of positions to mask
        for re-prediction.

        Parameters
        ----------
        step : int
            Current refinement step (0-indexed).

        Returns
        -------
        float
            Mask ratio.
        """
        t = step / max(self.num_refine_steps - 1, 1)

        if self.mask_ratio_schedule == "linear":
            return 0.5 * (1.0 - t) + 0.1 * t
        elif self.mask_ratio_schedule == "cosine":
            return 0.1 + 0.4 * (1.0 + math.cos(math.pi * t)) / 2.0
        else:
            return 0.3  # fallback constant

    def _predict_length(self, solution_flat: torch.Tensor) -> int:
        """Predict the output sequence length.

        Parameters
        ----------
        solution_flat : torch.Tensor
            Flattened solution tensor, shape ``(d_solution,)``.

        Returns
        -------
        int
            Predicted length, clamped to ``[1, max_output_len]``.
        """
        logits = self.length_predictor(solution_flat)  # (max_output_len,)
        predicted_len = logits.argmax().item() + 1
        return min(max(predicted_len, 1), self.max_output_len)

    def _initial_predict(
        self,
        solution_emb: torch.Tensor,
        length: int,
    ) -> torch.Tensor:
        """Generate initial token embeddings.

        Parameters
        ----------
        solution_emb : torch.Tensor
            Projected solution embedding, shape ``(d_model,)``.
        length : int
            Output sequence length.

        Returns
        -------
        torch.Tensor
            Initial token embeddings, shape ``(length, d_model)``.
        """
        positions = torch.arange(length, device=solution_emb.device)
        pos_emb = self.position_embeddings(positions)  # (length, d_model)

        # Broadcast solution to each position and combine
        solution_broadcast = solution_emb.unsqueeze(0).expand(length, -1)
        combined = torch.cat([solution_broadcast, pos_emb], dim=-1)
        return self.initial_predictor(combined)  # (length, d_model)

    def _refine_step(
        self,
        token_embs: torch.Tensor,
        solution_emb: torch.Tensor,
        mask_ratio: float,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Execute one refinement step (batched).

        1. Score confidence of each position.
        2. Mask the least confident positions.
        3. Re-predict masked positions via cross-attention to solution.

        Parameters
        ----------
        token_embs : torch.Tensor
            Current token embeddings, shape ``(B, length, d_model)``.
        solution_emb : torch.Tensor
            Solution embedding, shape ``(B, 1, d_model)`` (memory for
            cross-attention).
        mask_ratio : float
            Fraction of positions to mask.

        Returns
        -------
        tuple of (torch.Tensor, torch.Tensor)
            Updated token embeddings and per-position confidence scores.
        """
        B, length, d_model = token_embs.shape

        # Score confidence
        confidence = self.confidence_scorer(token_embs).squeeze(-1)  # (B, length)

        # Determine positions to mask (lowest confidence)
        num_mask = max(1, int(length * mask_ratio))
        _, mask_indices = confidence.topk(num_mask, dim=-1, largest=False)  # (B, num_mask)

        # Replace masked positions with mask embedding (vectorized batched scatter)
        batch_indices = torch.arange(B, device=token_embs.device).unsqueeze(1).expand(B, num_mask)
        masked_embs = token_embs.clone()
        masked_embs[batch_indices, mask_indices] = self.mask_embedding

        # Refine through decoder (cross-attend to solution)
        refined = self.refine_decoder(masked_embs, solution_emb)

        # Merge: keep unmasked positions, use refined for masked
        output = token_embs.clone()
        output[batch_indices, mask_indices] = refined[batch_indices, mask_indices]

        # Re-score confidence
        new_confidence = self.confidence_scorer(output).squeeze(-1)

        return output, new_confidence

    def forward(
        self,
        solution: SolutionTensor,
        max_length: Optional[int] = None,
        num_refine_steps: Optional[int] = None,
    ) -> torch.Tensor:
        """Decode a SolutionTensor to output token logits.

        Parameters
        ----------
        solution : SolutionTensor
            The algebraic solution to decode.
        max_length : int, optional
            Override output length. If None, length is predicted.
        num_refine_steps : int, optional
            Override number of refinement iterations.

        Returns
        -------
        torch.Tensor
            Output logits, shape ``(length, vocab_size)``.
        """
        refine_steps = num_refine_steps or self.num_refine_steps

        # Flatten solution tensor
        result = solution.result_tensor

        # Detect batch dimension: (10, d) vs (B, 10, d)
        if result.dim() == 3:
            # Batched mode: (B, 10, d)
            B = result.shape[0]
            solution_flat = result.reshape(B, -1)  # (B, d_solution)
        else:
            # Single mode: (10, d) → treat as B=1
            B = 1
            solution_flat = result.reshape(1, -1)  # (1, d_solution)

        # Pad or truncate to expected d_solution
        if solution_flat.shape[-1] < self.d_solution:
            padding = torch.zeros(
                B, self.d_solution - solution_flat.shape[-1],
                device=solution_flat.device,
                dtype=solution_flat.dtype,
            )
            solution_flat = torch.cat([solution_flat, padding], dim=-1)
        elif solution_flat.shape[-1] > self.d_solution:
            solution_flat = solution_flat[:, :self.d_solution]

        # Project solution to decoder space: (B, d_model)
        solution_emb = self.solution_proj(solution_flat)

        # Predict or use given length
        if max_length is None:
            # Predict per-batch-item and use the maximum
            logits = self.length_predictor(solution_flat)  # (B, max_output_len)
            predicted_lens = logits.argmax(dim=-1) + 1     # (B,)
            length = min(int(predicted_lens.max().item()), self.max_output_len)
        else:
            length = min(max_length, self.max_output_len)

        # Initial parallel prediction: (B, length, d_model)
        positions = torch.arange(length, device=solution_emb.device)
        pos_emb = self.position_embeddings(positions)  # (length, d_model)
        solution_broadcast = solution_emb.unsqueeze(1).expand(B, length, -1)  # (B, length, d_model)
        pos_broadcast = pos_emb.unsqueeze(0).expand(B, length, -1)  # (B, length, d_model)
        combined = torch.cat([solution_broadcast, pos_broadcast], dim=-1)  # (B, length, 2*d_model)
        token_embs = self.initial_predictor(combined)  # (B, length, d_model)

        # Solution as memory for cross-attention: (B, 1, d_model)
        solution_memory = solution_emb.unsqueeze(1)  # (B, 1, d_model)

        # Iterative refinement
        for step in range(refine_steps):
            mask_ratio = self._get_mask_ratio(step)
            token_embs, confidence = self._refine_step(
                token_embs, solution_memory, mask_ratio
            )

        # Project to vocabulary: (B, length, vocab_size) or (length, vocab_size)
        logits = self.vocab_head(token_embs)
        if result.dim() == 2:
            logits = logits.squeeze(0)  # Back to (length, vocab_size) for single inputs
        return logits

    def decode_to_tokens(
        self,
        solution: SolutionTensor,
        max_length: Optional[int] = None,
        temperature: float = 1.0,
    ) -> torch.Tensor:
        """Decode to discrete token indices (argmax or sampled).

        Parameters
        ----------
        solution : SolutionTensor
        max_length : int, optional
        temperature : float
            Sampling temperature. 0.0 = argmax, > 0 = sampling.

        Returns
        -------
        torch.Tensor
            Token indices, shape ``(length,)`` with dtype ``torch.long``.
        """
        logits = self.forward(solution, max_length)

        if temperature <= 0.0:
            return logits.argmax(dim=-1)
        else:
            probs = F.softmax(logits / temperature, dim=-1)
            return torch.multinomial(probs, num_samples=1).squeeze(-1)

    def extra_repr(self) -> str:
        return (
            f"d_solution={self.d_solution}, d_model={self.d_model}, "
            f"vocab_size={self.vocab_size}, max_output_len={self.max_output_len}, "
            f"num_refine_steps={self.num_refine_steps}"
        )
