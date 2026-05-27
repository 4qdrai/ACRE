"""
Concept Distillation — Self-supervised extraction of structured concepts from text.

This module implements the core F-LACA insight: raw unstructured text can be
*compressed* into dense 10-element Concept Tensors and Problem Tensors,
dramatically reducing the token count while preserving semantic content.

Think of it like a translator that turns a 3-page essay into a compact
structured card with exactly 10 well-defined slots — ontology, axioms,
relations, etc. — so the reasoning engine never has to wade through prose.

Classes:
    ElementEncoder: Encodes a single element slot from contextual embeddings.
    ConceptDistillationHead: Transformer head that predicts all 10 elements.
    TextToConceptPipeline: End-to-end pipeline from raw text to ConceptTensors.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from acre.core.concept_tensor import ConceptTensor, NUM_CONCEPT_ELEMENTS as NUM_ELEMENTS
from acre.core.problem_tensor import ProblemTensor

ELEMENT_DIM = 64           # Dimensionality per element slot
CONCEPT_TOTAL_DIM = NUM_ELEMENTS * ELEMENT_DIM  # 640


@dataclass
class CompressionStats:
    """Tracks how much the pipeline compresses input data."""
    input_tokens: int = 0
    output_elements: int = 0
    compression_ratio: float = 0.0
    processing_time_ms: float = 0.0

    def update(self, n_tokens: int, n_elements: int, elapsed_ms: float) -> None:
        self.input_tokens += n_tokens
        self.output_elements += n_elements
        total = self.input_tokens
        self.compression_ratio = total / max(self.output_elements, 1)
        self.processing_time_ms += elapsed_ms


# ---------------------------------------------------------------------------
# Model components
# ---------------------------------------------------------------------------

class ElementEncoder(nn.Module):
    """Learns to extract a *single* element slot from contextual embeddings.

    Each element encoder specialises on one of the 10 structural roles
    (ontology, abstraction level, axioms, …).  During training the
    supervision signal comes from algebraic consistency — no labels needed.
    """

    def __init__(self, input_dim: int = 768, element_dim: int = ELEMENT_DIM) -> None:
        super().__init__()
        self.proj = nn.Sequential(
            nn.Linear(input_dim, input_dim),
            nn.GELU(),
            nn.LayerNorm(input_dim),
            nn.Linear(input_dim, element_dim),
            nn.LayerNorm(element_dim),
        )

    def forward(self, hidden: Tensor) -> Tensor:
        """hidden: (batch, seq_len, input_dim) → (batch, element_dim)."""
        # Global average pooling then project
        pooled = hidden.mean(dim=1)
        return self.proj(pooled)


class ConceptDistillationHead(nn.Module):
    """Transformer head that produces all 10 element embeddings at once.

    Architecture:
        1. Shared context encoder (2-layer transformer)
        2. 10 parallel ElementEncoders — one per slot
        3. Optional cross-element attention for coherence
    """

    def __init__(
        self,
        input_dim: int = 768,
        element_dim: int = ELEMENT_DIM,
        n_elements: int = NUM_ELEMENTS,
        n_heads: int = 8,
        n_layers: int = 2,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.n_elements = n_elements
        self.element_dim = element_dim

        # Shared contextual transformer
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=input_dim, nhead=n_heads, dim_feedforward=input_dim * 4,
            dropout=dropout, activation="gelu", batch_first=True,
        )
        self.context_encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Per-element specialist encoders
        self.element_encoders = nn.ModuleList([
            ElementEncoder(input_dim, element_dim) for _ in range(n_elements)
        ])

        # Cross-element coherence layer
        self.cross_attn = nn.MultiheadAttention(
            embed_dim=element_dim, num_heads=4, batch_first=True,
        )
        self.cross_norm = nn.LayerNorm(element_dim)

    def forward(self, token_embeddings: Tensor) -> Tensor:
        """
        Args:
            token_embeddings: (batch, seq_len, input_dim) from a text encoder.

        Returns:
            concept_elements: (batch, 10, element_dim) — the 10-slot tensor.
        """
        ctx = self.context_encoder(token_embeddings)  # (B, S, D)

        # Extract each element independently
        elements = [enc(ctx) for enc in self.element_encoders]
        stacked = torch.stack(elements, dim=1)  # (B, 10, d)

        # Cross-element coherence: each element attends to all others
        refined, _ = self.cross_attn(stacked, stacked, stacked)
        stacked = self.cross_norm(stacked + refined)

        return stacked


class SmallTextEncoder(nn.Module):
    """Minimal text encoder: learned token embeddings + positional encoding +
    small transformer.  In production, swap for a pretrained encoder."""

    def __init__(
        self,
        vocab_size: int = 32_000,
        d_model: int = 768,
        n_heads: int = 8,
        n_layers: int = 4,
        max_len: int = 2048,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.tok_emb = nn.Embedding(vocab_size, d_model)
        self.pos_emb = nn.Embedding(max_len, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 4,
            dropout=dropout, activation="gelu", batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
        self.d_model = d_model

    def forward(self, token_ids: Tensor) -> Tensor:
        """token_ids: (batch, seq_len) long → (batch, seq_len, d_model)."""
        B, S = token_ids.shape
        positions = torch.arange(S, device=token_ids.device).unsqueeze(0).expand(B, -1)
        x = self.tok_emb(token_ids) * math.sqrt(self.d_model) + self.pos_emb(positions)
        return self.encoder(x)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class TextToConceptPipeline:
    """End-to-end pipeline: raw text → List[ConceptTensor].

    Usage::

        pipeline = TextToConceptPipeline(device="cuda")
        concepts = pipeline.extract_concepts("Relativity unifies space and time…")
        problems = pipeline.extract_problems("Given a free-falling observer…")
        print(pipeline.stats)
    """

    def __init__(
        self,
        vocab_size: int = 32_000,
        d_model: int = 768,
        element_dim: int = ELEMENT_DIM,
        max_len: int = 2048,
        device: str = "cpu",
    ) -> None:
        self.device = torch.device(device)
        self.max_len = max_len
        self.d_model = d_model

        self.text_encoder = SmallTextEncoder(
            vocab_size=vocab_size, d_model=d_model, max_len=max_len,
        ).to(self.device)

        self.concept_head = ConceptDistillationHead(
            input_dim=d_model, element_dim=element_dim,
        ).to(self.device)

        self.problem_head = ConceptDistillationHead(
            input_dim=d_model, element_dim=element_dim,
        ).to(self.device)

        self.stats = CompressionStats()

    # ------------------------------------------------------------------
    # Simple byte-pair-like tokeniser stub (replace with real tokeniser)
    # ------------------------------------------------------------------
    def _tokenise(self, text: str) -> Tensor:
        """Character-level hash tokenisation (placeholder)."""
        ids = [hash(ch) % 32_000 for ch in text]
        ids = ids[: self.max_len]
        return torch.tensor([ids], dtype=torch.long, device=self.device)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @torch.no_grad()
    def extract_concepts(self, text: str) -> List[ConceptTensor]:
        """Extract concept structures from raw text.

        Each paragraph/section may yield one ConceptTensor; for short inputs
        a single ConceptTensor is returned.

        Args:
            text: Free-form text describing one or more concepts.

        Returns:
            List of ConceptTensor, each with shape (10, element_dim).
        """
        t0 = time.perf_counter()
        token_ids = self._tokenise(text)
        n_tokens = token_ids.shape[1]

        hidden = self.text_encoder(token_ids)
        elements = self.concept_head(hidden)  # (1, 10, d)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.stats.update(n_tokens, NUM_ELEMENTS, elapsed_ms)

        concept = ConceptTensor.from_tensor(elements[0].cpu())
        concept.metadata = {"source_tokens": str(n_tokens), "compression": f"{n_tokens / NUM_ELEMENTS:.1f}x"}
        return [concept]

    @torch.no_grad()
    def extract_problems(self, text: str) -> List[ProblemTensor]:
        """Extract problem structures from raw text.

        Args:
            text: Free-form text describing one or more problems/tasks.

        Returns:
            List of ProblemTensor, each with shape (10, element_dim).
        """
        t0 = time.perf_counter()
        token_ids = self._tokenise(text)
        n_tokens = token_ids.shape[1]

        hidden = self.text_encoder(token_ids)
        elements = self.problem_head(hidden)  # (1, 10, d)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.stats.update(n_tokens, NUM_ELEMENTS, elapsed_ms)

        problem = ProblemTensor.from_tensor(elements[0].cpu())
        problem.metadata = {"source_tokens": str(n_tokens)}
        return [problem]

    @torch.no_grad()
    def extract_batch(
        self,
        texts: List[str],
        mode: str = "concept",
    ) -> List[ConceptTensor] | List[ProblemTensor]:
        """Batch extraction for efficiency.

        Args:
            texts: List of raw text strings.
            mode: ``"concept"`` or ``"problem"``.

        Returns:
            One tensor per input text.
        """
        results: list = []
        for txt in texts:
            if mode == "concept":
                results.extend(self.extract_concepts(txt))
            else:
                results.extend(self.extract_problems(txt))
        return results

    def get_compression_summary(self) -> Dict[str, float]:
        """Return a human-readable compression summary."""
        return {
            "total_input_tokens": self.stats.input_tokens,
            "total_output_elements": self.stats.output_elements,
            "compression_ratio": round(self.stats.compression_ratio, 1),
            "total_time_ms": round(self.stats.processing_time_ms, 2),
        }


# ---------------------------------------------------------------------------
# Standalone execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("ACRE — Concept Distillation Pipeline Demo")
    print("=" * 60)

    pipeline = TextToConceptPipeline(device="cpu")

    sample_texts = [
        "General relativity describes gravity as the curvature of spacetime "
        "caused by mass and energy. The Einstein field equations relate the "
        "geometry of spacetime to the distribution of matter within it.",
        "Gradient descent is an iterative optimisation algorithm used to "
        "minimise a differentiable loss function by moving in the direction "
        "of steepest descent as defined by the negative of the gradient.",
    ]

    print("\n--- Concept Extraction ---")
    for i, text in enumerate(sample_texts):
        concepts = pipeline.extract_concepts(text)
        c = concepts[0]
        print(f"  Text {i + 1}: {len(text)} chars -> ConceptTensor {tuple(c.to_tensor().shape)}")
        print(f"           metadata = {c.metadata}")

    print("\n--- Problem Extraction ---")
    problems = pipeline.extract_problems(
        "Determine the geodesic path of a photon near a Schwarzschild black hole."
    )
    p = problems[0]
    print(f"  ProblemTensor shape: {tuple(p.to_tensor().shape)}")

    print("\n--- Compression Summary ---")
    summary = pipeline.get_compression_summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\nDone [OK]")
