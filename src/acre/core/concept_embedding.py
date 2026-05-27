"""
Concept Embedding & Reranker — Dual Encoder + Cross-Encoder for Retrieval
=========================================================================

This module provides two complementary models for measuring concept-problem
similarity:

1. **ConceptEmbeddingModel** (Dual Encoder):
   Independently encodes concepts and problems into a shared embedding
   space, then measures similarity via dot product or cosine distance.
   Fast for large-scale retrieval (O(1) per query after encoding).

2. **ConceptReranker** (Cross-Encoder):
   Takes a concept-problem *pair* as input and produces a fine-grained
   relevance score. More accurate than the dual encoder but O(n) per
   query, so used only for reranking the top-k candidates.

Training Objective
------------------

The dual encoder is trained with **InfoNCE contrastive loss**:

.. math::

    \\mathcal{L}_{\\text{InfoNCE}} = -\\log
    \\frac{\\exp(\\text{sim}(q, k^+) / \\tau)}
    {\\sum_{i=0}^{N} \\exp(\\text{sim}(q, k_i) / \\tau)}

where :math:`q` is the query (problem embedding), :math:`k^+` is the
positive concept, :math:`k_i` are all concepts in the batch (including
negatives), and :math:`\\tau` is a learnable temperature.

The cross-encoder reranker is trained with binary cross-entropy on
(concept, problem, relevance) triples.

Retrieval Pipeline
------------------

.. code-block:: text

    Query Problem
         │
         ▼  Dual Encoder
    [Embed Problem]  →  Dot-product search over concept library
         │
         ▼  Top-K candidates
    [Cross-Encoder Reranker]  →  Fine-grained scoring
         │
         ▼
    Ranked concepts for LARE input
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Optional, Tuple

from acre.core.concept_tensor import ConceptTensor, NUM_CONCEPT_ELEMENTS, DEFAULT_EMBEDDING_DIM
from acre.core.problem_tensor import ProblemTensor, NUM_PROBLEM_ELEMENTS


class ConceptEmbeddingModel(nn.Module):
    """Dual encoder for concept-problem similarity.

    Independently encodes ConceptTensors and ProblemTensors into a shared
    :math:`d_{embed}`-dimensional space. Similarity is measured via cosine
    distance or dot product.

    Parameters
    ----------
    d_input : int
        Dimension per element of the input tensors (default 128).
    d_embed : int
        Output embedding dimension (default 256).
    num_elements : int
        Number of elements in input tensors (default 10).
    dropout : float
        Dropout rate (default 0.1).

    Examples
    --------
    >>> model = ConceptEmbeddingModel(d_input=64, d_embed=128)
    >>> c = ConceptTensor.random(d=64)
    >>> p = ProblemTensor.random(d=64)
    >>> sim = model.similarity(c, p)
    >>> -1 <= sim <= 1
    True
    """

    def __init__(
        self,
        d_input: int = DEFAULT_EMBEDDING_DIM,
        d_embed: int = 256,
        num_elements: int = NUM_CONCEPT_ELEMENTS,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.d_input = d_input
        self.d_embed = d_embed
        self.flat_dim = num_elements * d_input

        # ── Concept encoder arm ───────────────────────────────────
        self.concept_encoder = nn.Sequential(
            nn.Linear(self.flat_dim, self.flat_dim),
            nn.GELU(),
            nn.LayerNorm(self.flat_dim),
            nn.Dropout(dropout),
            nn.Linear(self.flat_dim, d_embed * 2),
            nn.GELU(),
            nn.LayerNorm(d_embed * 2),
            nn.Dropout(dropout),
            nn.Linear(d_embed * 2, d_embed),
            nn.LayerNorm(d_embed),
        )

        # ── Problem encoder arm ──────────────────────────────────
        self.problem_encoder = nn.Sequential(
            nn.Linear(self.flat_dim, self.flat_dim),
            nn.GELU(),
            nn.LayerNorm(self.flat_dim),
            nn.Dropout(dropout),
            nn.Linear(self.flat_dim, d_embed * 2),
            nn.GELU(),
            nn.LayerNorm(d_embed * 2),
            nn.Dropout(dropout),
            nn.Linear(d_embed * 2, d_embed),
            nn.LayerNorm(d_embed),
        )

        # ── Learnable temperature for InfoNCE ────────────────────
        self.log_temperature = nn.Parameter(torch.tensor(0.0))

        self._init_weights()

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)

    def embed_concept(self, c: ConceptTensor) -> torch.Tensor:
        """Encode a ConceptTensor into the shared embedding space.

        Parameters
        ----------
        c : ConceptTensor

        Returns
        -------
        torch.Tensor
            Shape ``(d_embed,)`` — L2-normalized embedding.
        """
        flat = c.to_tensor().reshape(-1)  # (10*d,)
        emb = self.concept_encoder(flat)
        return F.normalize(emb, p=2, dim=-1)

    def embed_concept_batch(self, concepts: List[ConceptTensor]) -> torch.Tensor:
        """Encode a batch of ConceptTensors.

        Parameters
        ----------
        concepts : list of ConceptTensor

        Returns
        -------
        torch.Tensor
            Shape ``(B, d_embed)`` — L2-normalized embeddings.
        """
        stacked = ConceptTensor.stack(concepts)  # (B, 10, d)
        flat = stacked.reshape(stacked.shape[0], -1)  # (B, 10*d)
        emb = self.concept_encoder(flat)
        return F.normalize(emb, p=2, dim=-1)

    def embed_problem(self, p: ProblemTensor) -> torch.Tensor:
        """Encode a ProblemTensor into the shared embedding space.

        Parameters
        ----------
        p : ProblemTensor

        Returns
        -------
        torch.Tensor
            Shape ``(d_embed,)`` — L2-normalized embedding.
        """
        flat = p.to_tensor().reshape(-1)
        emb = self.problem_encoder(flat)
        return F.normalize(emb, p=2, dim=-1)

    def embed_problem_batch(self, problems: List[ProblemTensor]) -> torch.Tensor:
        """Encode a batch of ProblemTensors.

        Parameters
        ----------
        problems : list of ProblemTensor

        Returns
        -------
        torch.Tensor
            Shape ``(B, d_embed)``.
        """
        stacked = ProblemTensor.stack(problems)
        flat = stacked.reshape(stacked.shape[0], -1)
        emb = self.problem_encoder(flat)
        return F.normalize(emb, p=2, dim=-1)

    def similarity(self, c: ConceptTensor, p: ProblemTensor) -> float:
        """Compute cosine similarity between a concept and a problem.

        Parameters
        ----------
        c : ConceptTensor
        p : ProblemTensor

        Returns
        -------
        float
            Similarity score in ``[-1, 1]``.
        """
        with torch.no_grad():
            c_emb = self.embed_concept(c)
            p_emb = self.embed_problem(p)
            return (c_emb @ p_emb).item()

    def batch_retrieve(
        self,
        query_problem: ProblemTensor,
        concept_library: List[ConceptTensor],
        top_k: int = 5,
    ) -> List[Tuple[ConceptTensor, float]]:
        """Retrieve the top-k most relevant concepts for a problem.

        Uses the dual encoder for fast nearest-neighbor search via
        dot product.

        Parameters
        ----------
        query_problem : ProblemTensor
            The query problem.
        concept_library : list of ConceptTensor
            The library of available concepts.
        top_k : int
            Number of concepts to retrieve.

        Returns
        -------
        list of (ConceptTensor, float)
            Top-k concepts with their similarity scores, sorted by
            decreasing similarity.
        """
        with torch.no_grad():
            q_emb = self.embed_problem(query_problem)              # (d_embed,)
            c_embs = self.embed_concept_batch(concept_library)     # (N, d_embed)
            scores = c_embs @ q_emb                                # (N,)

            k = min(top_k, len(concept_library))
            top_scores, top_indices = scores.topk(k)

            return [
                (concept_library[idx.item()], score.item())
                for idx, score in zip(top_indices, top_scores)
            ]

    def infonce_loss(
        self,
        concepts: List[ConceptTensor],
        problems: List[ProblemTensor],
    ) -> torch.Tensor:
        """Compute InfoNCE contrastive loss for training.

        Assumes ``concepts[i]`` is the positive match for ``problems[i]``.

        .. math::

            \\mathcal{L} = -\\frac{1}{B} \\sum_{i=1}^{B} \\log
            \\frac{\\exp(s_{ii} / \\tau)}{\\sum_{j=1}^{B} \\exp(s_{ij} / \\tau)}

        Parameters
        ----------
        concepts : list of ConceptTensor
            Batch of concepts (positives on the diagonal).
        problems : list of ProblemTensor
            Batch of problems (matched 1:1 with concepts).

        Returns
        -------
        torch.Tensor
            Scalar loss.
        """
        c_embs = self.embed_concept_batch(concepts)  # (B, d_embed)
        p_embs = self.embed_problem_batch(problems)  # (B, d_embed)

        # Temperature-scaled similarity matrix
        temperature = self.log_temperature.exp()
        sim_matrix = (c_embs @ p_embs.T) / temperature  # (B, B)

        B = sim_matrix.shape[0]
        labels = torch.arange(B, device=sim_matrix.device)

        # Symmetric InfoNCE: average c→p and p→c losses
        loss_cp = F.cross_entropy(sim_matrix, labels)
        loss_pc = F.cross_entropy(sim_matrix.T, labels)

        return 0.5 * (loss_cp + loss_pc)

    def forward(
        self, c: ConceptTensor, p: ProblemTensor
    ) -> torch.Tensor:
        """Compute similarity (forward pass for training).

        Returns
        -------
        torch.Tensor
            Scalar similarity score.
        """
        c_emb = self.embed_concept(c)
        p_emb = self.embed_problem(p)
        return (c_emb @ p_emb)


class ConceptReranker(nn.Module):
    """Cross-encoder reranker for fine-grained concept-problem scoring.

    Unlike the dual encoder (which encodes independently), the reranker
    takes a **concatenated** concept-problem pair and produces a single
    relevance score. This allows it to capture fine-grained interactions
    but makes it O(n) per query — suitable only for reranking.

    Parameters
    ----------
    d_input : int
        Dimension per element of input tensors (default 128).
    d_hidden : int
        Hidden dimension of the scoring MLP (default 512).
    num_elements : int
        Number of elements per tensor (default 10).
    num_layers : int
        Number of transformer layers for cross-encoding (default 2).
    num_heads : int
        Number of attention heads (default 4).
    dropout : float
        Dropout rate (default 0.1).

    Examples
    --------
    >>> reranker = ConceptReranker(d_input=64)
    >>> p = ProblemTensor.random(d=64)
    >>> concepts = [ConceptTensor.random(d=64) for _ in range(10)]
    >>> ranked = reranker.rerank(p, concepts)
    >>> len(ranked) == 10
    True
    """

    def __init__(
        self,
        d_input: int = DEFAULT_EMBEDDING_DIM,
        d_hidden: int = 512,
        num_elements: int = NUM_CONCEPT_ELEMENTS,
        num_layers: int = 2,
        num_heads: int = 4,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.d_input = d_input
        self.num_elements = num_elements
        self.pair_dim = 2 * num_elements  # 20 elements total (concept + problem)

        # Input projection: map each element to d_hidden
        self.element_proj = nn.Linear(d_input, d_hidden)

        # Transformer encoder for cross-element attention
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_hidden,
            nhead=num_heads,
            dim_feedforward=d_hidden * 2,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.cross_encoder = nn.TransformerEncoder(
            encoder_layer, num_layers=num_layers
        )

        # Scoring head: pool cross-encoded elements → single score
        self.score_head = nn.Sequential(
            nn.Linear(d_hidden, d_hidden // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_hidden // 2, 1),
            nn.Sigmoid(),
        )

        # Type embeddings: distinguish concept elements from problem elements
        self.type_embeddings = nn.Embedding(2, d_hidden)

        # Element position embeddings
        self.element_position = nn.Embedding(self.pair_dim, d_hidden)

        self._init_weights()

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Embedding):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def score_pair(
        self, problem: ProblemTensor, concept: ConceptTensor
    ) -> torch.Tensor:
        """Score a single concept-problem pair.

        Parameters
        ----------
        problem : ProblemTensor
        concept : ConceptTensor

        Returns
        -------
        torch.Tensor
            Scalar relevance score in ``[0, 1]``.
        """
        # Stack concept and problem elements: (20, d_input)
        c_elements = concept.to_tensor()  # (10, d_input)
        p_elements = problem.to_tensor()  # (10, d_input)
        all_elements = torch.cat([c_elements, p_elements], dim=0)  # (20, d_input)

        # Project to hidden dim
        projected = self.element_proj(all_elements)  # (20, d_hidden)

        # Add type embeddings (0 = concept, 1 = problem)
        type_ids = torch.cat([
            torch.zeros(self.num_elements, dtype=torch.long, device=projected.device),
            torch.ones(self.num_elements, dtype=torch.long, device=projected.device),
        ])
        projected = projected + self.type_embeddings(type_ids)

        # Add element position embeddings
        pos_ids = torch.arange(self.pair_dim, device=projected.device)
        projected = projected + self.element_position(pos_ids)

        # Cross-encode (batch dim = 1)
        encoded = self.cross_encoder(projected.unsqueeze(0))  # (1, 20, d_hidden)

        # Mean pool → score
        pooled = encoded.mean(dim=1)  # (1, d_hidden)
        score = self.score_head(pooled)  # (1, 1)

        return score.squeeze()

    def rerank(
        self,
        problem: ProblemTensor,
        candidates: List[ConceptTensor],
    ) -> List[Tuple[ConceptTensor, float]]:
        """Rerank candidate concepts by relevance to a problem.

        Parameters
        ----------
        problem : ProblemTensor
            The query problem.
        candidates : list of ConceptTensor
            Candidate concepts to rerank (typically top-k from dual encoder).

        Returns
        -------
        list of (ConceptTensor, float)
            Candidates sorted by decreasing relevance score.
        """
        scores = []
        with torch.no_grad():
            for concept in candidates:
                score = self.score_pair(problem, concept).item()
                scores.append((concept, score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def forward(
        self, problem: ProblemTensor, concept: ConceptTensor
    ) -> torch.Tensor:
        """Forward pass for training — returns relevance score."""
        return self.score_pair(problem, concept)
