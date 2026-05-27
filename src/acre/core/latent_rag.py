"""
Latent RAG — Self-Learning Knowledge Store for Concept-Problem-Solution Triples
===============================================================================

The **LatentRAG** is a non-parametric memory module that stores successful
concept-problem-solution triples for retrieval during inference. It enables
the ACRE system to *learn from its own successful reasoning traces* and
retrieve relevant prior solutions for new problems.

Architecture
------------

.. code-block:: text

    New Problem
         │
         ▼  embed
    [Query Embedding]  →  FAISS nearest-neighbor search
         │
         ▼  top-k
    [Retrieved Triples: (Concept, Problem, Solution)]
         │
         ▼
    [LARE]  ← augments reasoning with retrieved concepts/solutions

Self-Learning Loop
------------------

1. **Solve**: LARE produces a SolutionTensor for a given problem.
2. **Verify**: The solution is verified against the problem's formal spec.
3. **Store**: If verification passes, the (concept, problem, solution)
   triple is stored in the RAG.
4. **Retrieve**: On future problems, the RAG augments the concept set
   with relevant prior knowledge.

This creates a positive feedback loop where the system becomes more
capable over time without retraining the neural parameters.

Storage
-------

Keys are stored as dense embeddings (from the ConceptEmbeddingModel).
Values are the full (ConceptTensor, ProblemTensor, SolutionTensor) triples.

For efficient nearest-neighbor search, we use either FAISS (if available)
or a simple brute-force implementation as fallback.

Consolidation
-------------

When certain entries are retrieved frequently (above a threshold), they
can be *consolidated* into parametric knowledge — effectively distilling
the most useful retrieval patterns back into the model weights.
"""

from __future__ import annotations

import torch
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import time
import logging

from acre.core.concept_tensor import ConceptTensor, DEFAULT_EMBEDDING_DIM
from acre.core.problem_tensor import ProblemTensor
from acre.core.solution_tensor import SolutionTensor

logger = logging.getLogger(__name__)

# Try importing FAISS for efficient search; fall back to brute force
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.info(
        "FAISS not available. LatentRAG will use brute-force search. "
        "Install faiss-cpu or faiss-gpu for efficient retrieval."
    )


@dataclass
class RAGEntry:
    """A single entry in the Latent RAG store.

    Attributes
    ----------
    concept : ConceptTensor
        The concept used in the successful reasoning.
    problem : ProblemTensor
        The problem that was solved.
    solution : SolutionTensor
        The verified solution.
    embedding : torch.Tensor
        The dense embedding vector used as the retrieval key.
    timestamp : float
        When this entry was stored.
    retrieval_count : int
        How many times this entry has been retrieved.
    metadata : dict
        Additional info (source, verification details, etc.).
    """
    concept: ConceptTensor
    problem: ProblemTensor
    solution: SolutionTensor
    embedding: torch.Tensor
    timestamp: float = field(default_factory=time.time)
    retrieval_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class BruteForceIndex:
    """Fallback nearest-neighbor search using brute-force cosine similarity.

    Used when FAISS is not available. Supports the same basic interface
    as a FAISS index for our purposes.

    Parameters
    ----------
    d : int
        Embedding dimension.
    """

    def __init__(self, d: int) -> None:
        self.d = d
        self.vectors: List[np.ndarray] = []

    @property
    def ntotal(self) -> int:
        """Number of vectors in the index."""
        return len(self.vectors)

    def add(self, vectors: np.ndarray) -> None:
        """Add vectors to the index.

        Parameters
        ----------
        vectors : np.ndarray
            Shape ``(n, d)`` — vectors to add.
        """
        for i in range(vectors.shape[0]):
            self.vectors.append(vectors[i].copy())

    def search(
        self, query: np.ndarray, k: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Search for k nearest neighbors.

        Parameters
        ----------
        query : np.ndarray
            Shape ``(nq, d)`` — query vectors.
        k : int
            Number of neighbors to return.

        Returns
        -------
        tuple of (np.ndarray, np.ndarray)
            Distances and indices, each shape ``(nq, k)``.
        """
        if not self.vectors:
            nq = query.shape[0]
            return (
                np.full((nq, k), float("inf"), dtype=np.float32),
                np.full((nq, k), -1, dtype=np.int64),
            )

        db = np.stack(self.vectors, axis=0)  # (N, d)
        # Normalize for cosine similarity
        query_norm = query / (np.linalg.norm(query, axis=-1, keepdims=True) + 1e-8)
        db_norm = db / (np.linalg.norm(db, axis=-1, keepdims=True) + 1e-8)

        # Cosine similarity → distance = 1 - similarity
        sim = query_norm @ db_norm.T  # (nq, N)
        distances = 1.0 - sim

        # Get top-k (smallest distance = most similar)
        k = min(k, len(self.vectors))
        indices = np.argsort(distances, axis=-1)[:, :k]
        sorted_distances = np.take_along_axis(distances, indices, axis=-1)

        return sorted_distances.astype(np.float32), indices.astype(np.int64)

    def remove_ids(self, ids: np.ndarray) -> None:
        """Remove entries by index (replaces with None, compacts later)."""
        for idx in sorted(ids, reverse=True):
            if 0 <= idx < len(self.vectors):
                self.vectors.pop(idx)

    def reset(self) -> None:
        """Clear all vectors."""
        self.vectors = []


class LatentRAG:
    """Latent Retrieval-Augmented Generation store for self-learning.

    Stores verified concept-problem-solution triples and retrieves
    relevant prior knowledge for new problems via nearest-neighbor search.

    Parameters
    ----------
    d_embed : int
        Dimension of the embedding vectors used as keys.
    use_faiss : bool
        Whether to use FAISS for search. Falls back to brute force
        if FAISS is not installed.
    max_entries : int
        Maximum number of entries (FIFO eviction if exceeded).

    Examples
    --------
    >>> rag = LatentRAG(d_embed=256)
    >>> c = ConceptTensor.random(d=128)
    >>> p = ProblemTensor.random(d=128)
    >>> s = SolutionTensor.empty(d=128)
    >>> rag.store(c, p, s, embedding=torch.randn(256))
    >>> len(rag)
    1
    """

    def __init__(
        self,
        d_embed: int = 256,
        use_faiss: bool = True,
        max_entries: int = 100_000,
    ) -> None:
        self.d_embed = d_embed
        self.max_entries = max_entries
        self._entries: List[RAGEntry] = []

        # Build search index
        if use_faiss and FAISS_AVAILABLE:
            # Inner product index (for cosine similarity on normalized vectors)
            self._index = faiss.IndexFlatIP(d_embed)
            self._using_faiss = True
            logger.info(f"LatentRAG initialized with FAISS index (d={d_embed})")
        else:
            self._index = BruteForceIndex(d_embed)
            self._using_faiss = False
            logger.info(f"LatentRAG initialized with brute-force index (d={d_embed})")

        # Statistics tracking
        self._stats: Dict[str, Any] = {
            "total_stores": 0,
            "total_retrievals": 0,
            "total_hits": 0,
            "total_consolidations": 0,
            "total_deletions": 0,
        }

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def num_entries(self) -> int:
        """Number of stored triples."""
        return len(self._entries)

    # ── Store ─────────────────────────────────────────────────────

    def store(
        self,
        concept: ConceptTensor,
        problem: ProblemTensor,
        solution: SolutionTensor,
        embedding: Optional[torch.Tensor] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Store a verified concept-problem-solution triple.

        Parameters
        ----------
        concept : ConceptTensor
        problem : ProblemTensor
        solution : SolutionTensor
        embedding : torch.Tensor, optional
            The dense embedding key for retrieval. If not provided,
            a simple concatenation of concept and problem tensors is used.
        metadata : dict, optional
            Additional info to store with the entry.

        Returns
        -------
        int
            Index of the stored entry.
        """
        # Generate embedding if not provided
        if embedding is None:
            c_flat = concept.to_tensor().reshape(-1)
            p_flat = problem.to_tensor().reshape(-1)
            # Simple concat + project to d_embed
            combined = torch.cat([c_flat, p_flat])
            # Truncate or pad to d_embed
            if combined.shape[0] >= self.d_embed:
                embedding = combined[:self.d_embed]
            else:
                embedding = torch.cat([
                    combined,
                    torch.zeros(self.d_embed - combined.shape[0])
                ])
            embedding = torch.nn.functional.normalize(embedding, p=2, dim=-1)

        # Ensure embedding is the right shape
        if embedding.shape[0] != self.d_embed:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.d_embed}, "
                f"got {embedding.shape[0]}"
            )

        # Evict oldest if at capacity
        if len(self._entries) >= self.max_entries:
            self._evict_oldest(1)

        # Create entry
        entry = RAGEntry(
            concept=concept,
            problem=problem,
            solution=solution,
            embedding=embedding.detach().clone(),
            timestamp=time.time(),
            metadata=metadata or {},
        )

        # Add to index
        emb_np = embedding.detach().cpu().numpy().reshape(1, -1).astype(np.float32)
        self._index.add(emb_np)

        # Add to entries list
        idx = len(self._entries)
        self._entries.append(entry)

        self._stats["total_stores"] += 1
        logger.debug(f"Stored entry {idx} in LatentRAG (total: {len(self._entries)})")

        return idx

    # ── Retrieve ──────────────────────────────────────────────────

    def retrieve(
        self,
        query: torch.Tensor,
        top_k: int = 5,
    ) -> List[Tuple[ConceptTensor, ProblemTensor, SolutionTensor, float]]:
        """Retrieve the top-k most similar triples.

        Parameters
        ----------
        query : torch.Tensor
            Query embedding, shape ``(d_embed,)``.
        top_k : int
            Number of results to return.

        Returns
        -------
        list of (ConceptTensor, ProblemTensor, SolutionTensor, float)
            Retrieved triples with similarity scores, sorted by
            decreasing similarity.
        """
        if len(self._entries) == 0:
            return []

        self._stats["total_retrievals"] += 1

        # Normalize query
        query = torch.nn.functional.normalize(query, p=2, dim=-1)
        query_np = query.detach().cpu().numpy().reshape(1, -1).astype(np.float32)

        k = min(top_k, len(self._entries))
        distances, indices = self._index.search(query_np, k)

        results = []
        for i in range(k):
            idx = int(indices[0, i])
            if idx < 0 or idx >= len(self._entries):
                continue
            dist = float(distances[0, i])
            # Convert distance to similarity (for brute force: sim = 1 - dist)
            if self._using_faiss:
                similarity = dist  # FAISS IP already returns similarity
            else:
                similarity = 1.0 - dist

            entry = self._entries[idx]
            entry.retrieval_count += 1
            self._stats["total_hits"] += 1

            results.append((
                entry.concept,
                entry.problem,
                entry.solution,
                similarity,
            ))

        return results

    # ── Consolidate ───────────────────────────────────────────────

    def consolidate(self, threshold: int = 10) -> List[int]:
        """Identify frequently-retrieved entries for consolidation.

        Entries that have been retrieved more than ``threshold`` times
        are candidates for *parametric consolidation* — distilling their
        knowledge back into the model weights.

        Parameters
        ----------
        threshold : int
            Minimum retrieval count to qualify for consolidation.

        Returns
        -------
        list of int
            Indices of entries qualifying for consolidation.
        """
        candidates = []
        for i, entry in enumerate(self._entries):
            if entry.retrieval_count >= threshold:
                candidates.append(i)

        self._stats["total_consolidations"] += len(candidates)

        if candidates:
            logger.info(
                f"Found {len(candidates)} entries for consolidation "
                f"(threshold={threshold})"
            )

        return candidates

    def get_consolidation_data(
        self, indices: List[int]
    ) -> List[Tuple[ConceptTensor, ProblemTensor, SolutionTensor]]:
        """Extract triples for consolidation training.

        Parameters
        ----------
        indices : list of int
            Indices from ``consolidate()``.

        Returns
        -------
        list of (ConceptTensor, ProblemTensor, SolutionTensor)
        """
        return [
            (self._entries[i].concept, self._entries[i].problem, self._entries[i].solution)
            for i in indices
            if 0 <= i < len(self._entries)
        ]

    # ── Forget ────────────────────────────────────────────────────

    def forget(self, key: torch.Tensor, threshold: float = 0.95) -> int:
        """Delete entries similar to the given key.

        Removes all entries whose similarity to ``key`` exceeds the
        threshold. Useful for correcting wrong entries or removing
        outdated knowledge.

        Parameters
        ----------
        key : torch.Tensor
            Shape ``(d_embed,)`` — embedding of the entry to forget.
        threshold : float
            Similarity threshold for deletion (default 0.95).

        Returns
        -------
        int
            Number of entries deleted.
        """
        if len(self._entries) == 0:
            return 0

        key = torch.nn.functional.normalize(key, p=2, dim=-1)
        key_np = key.detach().cpu().numpy().reshape(1, -1).astype(np.float32)

        # Search for very similar entries
        k = min(100, len(self._entries))
        distances, indices = self._index.search(key_np, k)

        to_delete = []
        for i in range(k):
            idx = int(indices[0, i])
            if idx < 0 or idx >= len(self._entries):
                continue
            if self._using_faiss:
                sim = float(distances[0, i])
            else:
                sim = 1.0 - float(distances[0, i])

            if sim >= threshold:
                to_delete.append(idx)

        if to_delete:
            self._remove_entries(to_delete)
            self._stats["total_deletions"] += len(to_delete)
            logger.info(f"Forgot {len(to_delete)} entries from LatentRAG")

        return len(to_delete)

    # ── Self-Learning Loop ────────────────────────────────────────

    def self_learning_step(
        self,
        concept: ConceptTensor,
        problem: ProblemTensor,
        solution: SolutionTensor,
        embedding: Optional[torch.Tensor] = None,
    ) -> bool:
        """Execute one self-learning step: verify → store if passes.

        This is the core self-improvement mechanism. If the solution
        passes formal verification against the problem, it's stored
        in the RAG for future retrieval.

        Parameters
        ----------
        concept : ConceptTensor
        problem : ProblemTensor
        solution : SolutionTensor
        embedding : torch.Tensor, optional
            Precomputed embedding.

        Returns
        -------
        bool
            ``True`` if the solution was verified and stored.
        """
        # Verify the solution
        passed = solution.verify(problem)

        if passed:
            self.store(
                concept, problem, solution,
                embedding=embedding,
                metadata={"source": "self_learning", "verified": True},
            )
            logger.debug("Self-learning: solution verified and stored")
            return True
        else:
            logger.debug("Self-learning: solution failed verification, not stored")
            return False

    # ── Statistics ────────────────────────────────────────────────

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics about the RAG store.

        Returns
        -------
        dict
            Contains store/retrieval counts, hit rates, entry stats, etc.
        """
        if not self._entries:
            return {
                **self._stats,
                "num_entries": 0,
                "avg_retrieval_count": 0.0,
                "max_retrieval_count": 0,
            }

        retrieval_counts = [e.retrieval_count for e in self._entries]
        return {
            **self._stats,
            "num_entries": len(self._entries),
            "avg_retrieval_count": sum(retrieval_counts) / len(retrieval_counts),
            "max_retrieval_count": max(retrieval_counts),
            "min_retrieval_count": min(retrieval_counts),
            "hit_rate": (
                self._stats["total_hits"] / max(self._stats["total_retrievals"], 1)
            ),
        }

    # ── Internal Helpers ──────────────────────────────────────────

    def _evict_oldest(self, n: int) -> None:
        """Remove the n oldest entries (FIFO eviction)."""
        self._remove_entries(list(range(min(n, len(self._entries)))))

    def _remove_entries(self, indices: List[int]) -> None:
        """Remove entries by index and rebuild the search index."""
        if not indices:
            return

        # Remove entries (in reverse order to preserve indices)
        for idx in sorted(indices, reverse=True):
            if 0 <= idx < len(self._entries):
                self._entries.pop(idx)

        # Rebuild the index from remaining entries
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Rebuild the search index from current entries."""
        if self._using_faiss:
            self._index = faiss.IndexFlatIP(self.d_embed)
        else:
            self._index = BruteForceIndex(self.d_embed)

        if self._entries:
            embeddings = np.stack([
                e.embedding.cpu().numpy() for e in self._entries
            ], axis=0).astype(np.float32)
            self._index.add(embeddings)

    def clear(self) -> None:
        """Remove all entries and reset statistics."""
        self._entries.clear()
        self._rebuild_index()
        self._stats = {k: 0 for k in self._stats}
        logger.info("LatentRAG cleared")

    def __repr__(self) -> str:
        return (
            f"LatentRAG(d_embed={self.d_embed}, "
            f"entries={len(self._entries)}, "
            f"using_faiss={self._using_faiss})"
        )
