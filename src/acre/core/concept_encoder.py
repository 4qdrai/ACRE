"""
Concept Encoder — Translational Semantic Encoder
=================================================

The **ConceptEncoder** and **ProblemEncoder** are the entry points of the
F-LACA pipeline. They take unstructured input (represented as token
embeddings) and compress it into the structured 10-element manifold
representations required by the LARE.

Architecture
------------

Both encoders share a lightweight transformer backbone (configurable, default
4 layers / 256 hidden / 4 heads) followed by 10 separate projection heads —
one per element of the target tensor.

.. code-block:: text

    Input tokens (seq_len, d_model)
         │
         ▼
    [Shared Transformer Backbone]  ← 4 layers, 256 hidden, 4 heads
         │
         ├──► Head 0 → ontological_scaffolding / core_definition    (d,)
         ├──► Head 1 → abstraction_level / architecture             (d,)
         ├──► Head 2 → axiomatic_base / formal_requirements         (d,)
         ├──► ...
         └──► Head 9 → inter_concept_relations / related_problems   (d,)

The backbone extracts shared representations from the input sequence,
while the heads specialize in extracting each semantic facet.

Design Decision: Shared vs. Separate Backbones
----------------------------------------------

We use a **shared backbone with separate heads** rather than fully separate
encoders because:

1. The 10 elements are semantically related — the backbone can learn shared
   representations that benefit all heads.
2. It's parameter-efficient: 10 × full encoder would be ~10x the parameters.
3. The projection heads are lightweight (2-layer MLPs), so the specialization
   cost is minimal.

The ConceptEncoder and ProblemEncoder are separate classes with independent
backbones because concepts and problems have fundamentally different
semantics — sharing weights between them would be counterproductive.
"""

from __future__ import annotations

import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, Tuple

from acre.core.concept_tensor import (
    ConceptTensor,
    NUM_CONCEPT_ELEMENTS,
    CONCEPT_ELEMENT_NAMES,
    DEFAULT_EMBEDDING_DIM,
)
from acre.core.problem_tensor import (
    ProblemTensor,
    NUM_PROBLEM_ELEMENTS,
    PROBLEM_ELEMENT_NAMES,
)


class PositionalEncoding(nn.Module):
    """Sinusoidal positional encoding for transformer inputs.

    Adds position-dependent signals to input embeddings so the
    transformer can distinguish token positions (since self-attention
    is permutation-invariant otherwise).

    .. math::

        PE(pos, 2i) = \\sin\\left(\\frac{pos}{10000^{2i/d}}\\right) \\\\
        PE(pos, 2i+1) = \\cos\\left(\\frac{pos}{10000^{2i/d}}\\right)

    Parameters
    ----------
    d_model : int
        Model / embedding dimension.
    max_len : int
        Maximum sequence length to precompute.
    dropout : float
        Dropout rate applied after adding positional encoding.
    """

    def __init__(
        self, d_model: int = 256, max_len: int = 2048, dropout: float = 0.1
    ) -> None:
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)

        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2, dtype=torch.float32)
            * (-math.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)  # (1, max_len, d_model)
        self.register_buffer("pe", pe)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Add positional encoding to input.

        Parameters
        ----------
        x : torch.Tensor
            Shape ``(B, seq_len, d_model)``.

        Returns
        -------
        torch.Tensor
            Same shape, with positional encoding added.
        """
        x = x + self.pe[:, : x.size(1), :]
        return self.dropout(x)


class ProjectionHead(nn.Module):
    """A lightweight 2-layer MLP that projects backbone features to a
    single element of the target tensor.

    Parameters
    ----------
    d_backbone : int
        Backbone output dimension.
    d_target : int
        Target element dimension (d).
    dropout : float
        Dropout rate.
    """

    def __init__(
        self, d_backbone: int, d_target: int, dropout: float = 0.1
    ) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d_backbone, d_backbone),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.LayerNorm(d_backbone),
            nn.Linear(d_backbone, d_target),
            nn.LayerNorm(d_target),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Project backbone features to target dimension.

        Parameters
        ----------
        x : torch.Tensor
            Shape ``(B, d_backbone)`` — pooled backbone output.

        Returns
        -------
        torch.Tensor
            Shape ``(B, d_target)``.
        """
        return self.net(x)


class ConceptEncoder(nn.Module):
    """Translational Semantic Encoder for Concepts.

    Encodes token embeddings into a 10-element ConceptTensor by passing
    them through a transformer backbone and 10 specialized projection
    heads.

    Parameters
    ----------
    d_model : int
        Transformer hidden dimension (default 256).
    d_concept : int
        Output dimension per concept element (default 128).
    num_layers : int
        Number of transformer encoder layers (default 4).
    num_heads : int
        Number of attention heads (default 4).
    dim_feedforward : int
        Feedforward dimension in transformer layers (default 512).
    dropout : float
        Dropout rate (default 0.1).
    max_seq_len : int
        Maximum input sequence length (default 2048).

    Examples
    --------
    >>> encoder = ConceptEncoder(d_model=256, d_concept=64)
    >>> tokens = torch.randn(2, 100, 256)  # batch=2, seq=100
    >>> concept = encoder.encode_concept(tokens[0:1])
    >>> concept.dim
    64
    """

    def __init__(
        self,
        d_model: int = 256,
        d_concept: int = DEFAULT_EMBEDDING_DIM,
        num_layers: int = 4,
        num_heads: int = 4,
        dim_feedforward: int = 512,
        dropout: float = 0.1,
        max_seq_len: int = 2048,
    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.d_concept = d_concept
        self.num_elements = NUM_CONCEPT_ELEMENTS

        # Extra positions for CLS tokens prepended before the sequence
        self.pos_encoding = PositionalEncoding(d_model, max_seq_len + NUM_CONCEPT_ELEMENTS, dropout)

        # Transformer backbone
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=num_heads,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,  # Pre-LN for training stability
        )
        self.backbone = nn.TransformerEncoder(
            encoder_layer, num_layers=num_layers
        )

        # 10 CLS tokens — learnable queries, one per element
        self.cls_tokens = nn.Parameter(
            torch.randn(1, NUM_CONCEPT_ELEMENTS, d_model) * 0.02
        )

        # 10 projection heads
        self.projection_heads = nn.ModuleList([
            ProjectionHead(d_model, d_concept, dropout)
            for _ in range(NUM_CONCEPT_ELEMENTS)
        ])

        # Layer norm before projection
        self.pre_proj_norm = nn.LayerNorm(d_model)

        self._init_weights()

    def _init_weights(self) -> None:
        """Initialize weights with Xavier uniform."""
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)

    def encode_concept(
        self,
        text_tokens: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> ConceptTensor:
        """Encode input tokens into a ConceptTensor.

        Parameters
        ----------
        text_tokens : torch.Tensor
            Shape ``(1, seq_len, d_model)`` or ``(seq_len, d_model)``.
            If 2-D, a batch dimension is added automatically.
        attention_mask : torch.Tensor, optional
            Shape ``(seq_len,)`` or ``(1, seq_len)``. Boolean mask where
            ``True`` means the position should be *ignored* (padding).

        Returns
        -------
        ConceptTensor
            The encoded concept with dimension ``d_concept``.
        """
        if text_tokens.ndim == 2:
            text_tokens = text_tokens.unsqueeze(0)

        B, seq_len, _ = text_tokens.shape

        # Prepend CLS tokens (10 per sample)
        cls = self.cls_tokens.expand(B, -1, -1)  # (B, 10, d_model)
        x = torch.cat([cls, text_tokens], dim=1)  # (B, 10 + seq_len, d_model)

        # Add positional encoding
        x = self.pos_encoding(x)

        # Adjust attention mask for CLS tokens
        if attention_mask is not None:
            if attention_mask.ndim == 1:
                attention_mask = attention_mask.unsqueeze(0)
            cls_mask = torch.zeros(
                B, NUM_CONCEPT_ELEMENTS,
                dtype=attention_mask.dtype, device=attention_mask.device
            )
            attention_mask = torch.cat([cls_mask, attention_mask], dim=1)

        # Run through backbone
        x = self.backbone(x, src_key_padding_mask=attention_mask)

        # Extract CLS token representations
        cls_outputs = x[:, :NUM_CONCEPT_ELEMENTS, :]  # (B, 10, d_model)
        cls_outputs = self.pre_proj_norm(cls_outputs)

        # Project each CLS token through its head
        elements = {}
        for k, name in enumerate(CONCEPT_ELEMENT_NAMES):
            projected = self.projection_heads[k](cls_outputs[:, k, :])  # (B, d_concept)
            elements[name] = projected.squeeze(0)  # (d_concept,)

        return ConceptTensor(**elements)

    def forward(
        self,
        text_tokens: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> ConceptTensor:
        """Alias for ``encode_concept``."""
        return self.encode_concept(text_tokens, attention_mask)


class ProblemEncoder(nn.Module):
    """Translational Semantic Encoder for Problems (GPFs).

    Same architecture as ConceptEncoder but with its own backbone weights
    and projection heads mapping to the 10 GPF elements.

    Parameters
    ----------
    d_model : int
        Transformer hidden dimension (default 256).
    d_problem : int
        Output dimension per problem element (default 128).
    num_layers : int
        Number of transformer encoder layers (default 4).
    num_heads : int
        Number of attention heads (default 4).
    dim_feedforward : int
        Feedforward dimension (default 512).
    dropout : float
        Dropout rate (default 0.1).
    max_seq_len : int
        Maximum input sequence length (default 2048).
    """

    def __init__(
        self,
        d_model: int = 256,
        d_problem: int = DEFAULT_EMBEDDING_DIM,
        num_layers: int = 4,
        num_heads: int = 4,
        dim_feedforward: int = 512,
        dropout: float = 0.1,
        max_seq_len: int = 2048,
    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.d_problem = d_problem
        self.num_elements = NUM_PROBLEM_ELEMENTS

        # Extra positions for CLS tokens prepended before the sequence
        self.pos_encoding = PositionalEncoding(d_model, max_seq_len + NUM_PROBLEM_ELEMENTS, dropout)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=num_heads,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.backbone = nn.TransformerEncoder(
            encoder_layer, num_layers=num_layers
        )

        # 10 CLS tokens for problem elements
        self.cls_tokens = nn.Parameter(
            torch.randn(1, NUM_PROBLEM_ELEMENTS, d_model) * 0.02
        )

        self.projection_heads = nn.ModuleList([
            ProjectionHead(d_model, d_problem, dropout)
            for _ in range(NUM_PROBLEM_ELEMENTS)
        ])

        self.pre_proj_norm = nn.LayerNorm(d_model)
        self._init_weights()

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)

    def encode_problem(
        self,
        text_tokens: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> ProblemTensor:
        """Encode input tokens into a ProblemTensor.

        Parameters
        ----------
        text_tokens : torch.Tensor
            Shape ``(1, seq_len, d_model)`` or ``(seq_len, d_model)``.
        attention_mask : torch.Tensor, optional
            Padding mask.

        Returns
        -------
        ProblemTensor
        """
        if text_tokens.ndim == 2:
            text_tokens = text_tokens.unsqueeze(0)

        B, seq_len, _ = text_tokens.shape
        cls = self.cls_tokens.expand(B, -1, -1)
        x = torch.cat([cls, text_tokens], dim=1)
        x = self.pos_encoding(x)

        if attention_mask is not None:
            if attention_mask.ndim == 1:
                attention_mask = attention_mask.unsqueeze(0)
            cls_mask = torch.zeros(
                B, NUM_PROBLEM_ELEMENTS,
                dtype=attention_mask.dtype, device=attention_mask.device
            )
            attention_mask = torch.cat([cls_mask, attention_mask], dim=1)

        x = self.backbone(x, src_key_padding_mask=attention_mask)
        cls_outputs = x[:, :NUM_PROBLEM_ELEMENTS, :]
        cls_outputs = self.pre_proj_norm(cls_outputs)

        elements = {}
        for k, name in enumerate(PROBLEM_ELEMENT_NAMES):
            projected = self.projection_heads[k](cls_outputs[:, k, :])
            elements[name] = projected.squeeze(0)

        return ProblemTensor(**elements)

    def forward(
        self,
        text_tokens: torch.Tensor,
        attention_mask: Optional[torch.Tensor] = None,
    ) -> ProblemTensor:
        """Alias for ``encode_problem``."""
        return self.encode_problem(text_tokens, attention_mask)
