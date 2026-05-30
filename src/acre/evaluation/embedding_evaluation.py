"""
Embedding Evaluation — Quality metrics for concept and problem embeddings.

Good embeddings are the foundation of everything in ACRE: retrieval, algebra,
composition.  This module evaluates whether our concept embeddings actually
capture meaningful structure by measuring:

    1. Retrieval quality: Can we find the right concept for a given problem?
       (MRR, Recall@K, nDCG)
    2. Clustering quality: Do similar concepts cluster together?
       (Silhouette score, Adjusted Rand Index)
    3. Visualization: What does the concept space look like?
       (t-SNE / UMAP projections)

Classes:
    RetrievalMetrics: MRR, Recall@K, nDCG computation.
    ClusteringMetrics: Silhouette, ARI, cluster purity.
    EmbeddingVisualizer: t-SNE and UMAP visualization.
    EmbeddingEvaluator: Orchestrates all evaluations.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F
from torch import Tensor

logger = logging.getLogger(__name__)

NUM_ELEMENTS = 10
ELEMENT_DIM = 64


# ---------------------------------------------------------------------------
# Retrieval metrics
# ---------------------------------------------------------------------------

class RetrievalMetrics:
    """Information retrieval metrics for concept embeddings.

    Given a query embedding and a corpus of candidate embeddings with
    known relevance labels, computes standard retrieval metrics.
    """

    @staticmethod
    def mrr(
        query_embeds: Tensor,
        corpus_embeds: Tensor,
        relevance: Tensor,
    ) -> float:
        """Mean Reciprocal Rank — average of 1/rank of first relevant result.

        Args:
            query_embeds: (Q, D) query vectors.
            corpus_embeds: (C, D) corpus vectors.
            relevance: (Q, C) binary relevance matrix.

        Returns:
            MRR score in [0, 1].
        """
        # Compute similarity matrix
        sims = F.cosine_similarity(
            query_embeds.unsqueeze(1),
            corpus_embeds.unsqueeze(0),
            dim=-1,
        )  # (Q, C)

        # Rank by similarity (descending)
        ranks = sims.argsort(dim=1, descending=True)  # (Q, C)
        reciprocal_ranks = []

        for q in range(query_embeds.size(0)):
            relevant = relevance[q].bool()
            for rank_pos, corpus_idx in enumerate(ranks[q]):
                if relevant[corpus_idx]:
                    reciprocal_ranks.append(1.0 / (rank_pos + 1))
                    break
            else:
                reciprocal_ranks.append(0.0)

        return sum(reciprocal_ranks) / len(reciprocal_ranks)

    @staticmethod
    def recall_at_k(
        query_embeds: Tensor,
        corpus_embeds: Tensor,
        relevance: Tensor,
        k: int = 5,
    ) -> float:
        """Recall@K — fraction of relevant items found in top K.

        Args:
            query_embeds: (Q, D)
            corpus_embeds: (C, D)
            relevance: (Q, C) binary
            k: Number of top results to consider.

        Returns:
            Recall@K in [0, 1].
        """
        sims = F.cosine_similarity(
            query_embeds.unsqueeze(1),
            corpus_embeds.unsqueeze(0),
            dim=-1,
        )
        topk_indices = sims.topk(k, dim=1).indices  # (Q, K)
        recalls = []

        for q in range(query_embeds.size(0)):
            relevant = relevance[q].bool()
            n_relevant = relevant.sum().item()
            if n_relevant == 0:
                continue
            retrieved_relevant = sum(
                1 for idx in topk_indices[q] if relevant[idx]
            )
            recalls.append(retrieved_relevant / n_relevant)

        return sum(recalls) / max(len(recalls), 1)

    @staticmethod
    def ndcg(
        query_embeds: Tensor,
        corpus_embeds: Tensor,
        relevance: Tensor,
        k: int = 10,
    ) -> float:
        """Normalised Discounted Cumulative Gain at K.

        Args:
            query_embeds: (Q, D)
            corpus_embeds: (C, D)
            relevance: (Q, C) graded relevance (can be non-binary)
            k: Number of positions to evaluate.

        Returns:
            nDCG@K in [0, 1].
        """
        sims = F.cosine_similarity(
            query_embeds.unsqueeze(1),
            corpus_embeds.unsqueeze(0),
            dim=-1,
        )
        ranks = sims.argsort(dim=1, descending=True)[:, :k]

        ndcg_scores = []
        for q in range(query_embeds.size(0)):
            # DCG
            dcg = 0.0
            for pos, idx in enumerate(ranks[q]):
                rel = relevance[q, idx].item()
                dcg += rel / math.log2(pos + 2)

            # IDCG (ideal ranking)
            ideal_rels = relevance[q].sort(descending=True).values[:k]
            idcg = sum(rel.item() / math.log2(pos + 2) for pos, rel in enumerate(ideal_rels))

            ndcg_scores.append(dcg / max(idcg, 1e-10))

        return sum(ndcg_scores) / max(len(ndcg_scores), 1)


# ---------------------------------------------------------------------------
# Clustering metrics
# ---------------------------------------------------------------------------

class ClusteringMetrics:
    """Clustering quality metrics for concept embeddings."""

    @staticmethod
    def silhouette_score(
        embeddings: Tensor,
        labels: Tensor,
    ) -> float:
        """Silhouette score — measures how well clusters are separated.

        Range: [-1, 1].  Higher = better separation.

        Args:
            embeddings: (N, D) data points.
            labels: (N,) cluster assignments.
        """
        N = embeddings.size(0)
        if N < 3:
            return 0.0

        # Pairwise distance matrix
        dists = torch.cdist(embeddings, embeddings)

        unique_labels = labels.unique()
        if len(unique_labels) < 2:
            return 0.0

        silhouettes = []
        for i in range(N):
            own_label = labels[i].item()
            own_cluster = labels == own_label
            other_labels = [l.item() for l in unique_labels if l.item() != own_label]

            # a(i): mean distance to same cluster
            same_dists = dists[i][own_cluster]
            if same_dists.numel() <= 1:
                a_i = 0.0
            else:
                a_i = same_dists.sum().item() / (same_dists.numel() - 1)

            # b(i): min mean distance to other clusters
            b_i = float("inf")
            for other_label in other_labels:
                other_cluster = labels == other_label
                other_dists = dists[i][other_cluster]
                if other_dists.numel() > 0:
                    b_i = min(b_i, other_dists.mean().item())

            if b_i == float("inf"):
                b_i = 0.0

            s_i = (b_i - a_i) / max(a_i, b_i, 1e-10)
            silhouettes.append(s_i)

        return sum(silhouettes) / len(silhouettes)

    @staticmethod
    def adjusted_rand_index(
        labels_true: Tensor,
        labels_pred: Tensor,
    ) -> float:
        """Adjusted Rand Index — measures agreement between two clusterings.

        Range: [-1, 1].  1 = perfect agreement, 0 = random.
        """
        n = labels_true.size(0)
        if n < 2:
            return 0.0

        # Build contingency table
        true_unique = labels_true.unique()
        pred_unique = labels_pred.unique()
        contingency = torch.zeros(len(true_unique), len(pred_unique))

        for i, t in enumerate(true_unique):
            for j, p in enumerate(pred_unique):
                contingency[i, j] = ((labels_true == t) & (labels_pred == p)).sum().item()

        # Compute ARI
        sum_comb_c = sum(
            contingency[i, j].item() * (contingency[i, j].item() - 1) / 2
            for i in range(len(true_unique))
            for j in range(len(pred_unique))
        )
        sum_comb_a = sum(
            contingency[i].sum().item() * (contingency[i].sum().item() - 1) / 2
            for i in range(len(true_unique))
        )
        sum_comb_b = sum(
            contingency[:, j].sum().item() * (contingency[:, j].sum().item() - 1) / 2
            for j in range(len(pred_unique))
        )

        n_comb = n * (n - 1) / 2
        expected = sum_comb_a * sum_comb_b / max(n_comb, 1e-10)
        max_index = (sum_comb_a + sum_comb_b) / 2
        denominator = max_index - expected

        if abs(denominator) < 1e-10:
            return 0.0

        return (sum_comb_c - expected) / denominator


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

class EmbeddingVisualizer:
    """Generates t-SNE / UMAP visualizations of the concept space."""

    @staticmethod
    def tsne_2d(
        embeddings: Tensor,
        labels: Optional[Tensor] = None,
        perplexity: float = 30.0,
        n_iter: int = 500,
    ) -> Tensor:
        """Simple t-SNE implementation for visualization.

        This is a minimal implementation; for production use scikit-learn.

        Args:
            embeddings: (N, D) high-dimensional embeddings.
            labels: Optional (N,) for coloring.
            perplexity: t-SNE perplexity parameter.
            n_iter: Number of optimization iterations.

        Returns:
            (N, 2) 2D coordinates.
        """
        N, D = embeddings.shape

        # Initialize with PCA-like reduction
        U, S, V = torch.pca_lowrank(embeddings, q=2)
        Y = U[:, :2] * S[:2]
        Y = Y + torch.randn_like(Y) * 0.01

        lr = 200.0
        momentum = 0.5

        # Pairwise distances in high-D
        dist_hd = torch.cdist(embeddings, embeddings)
        # Convert to conditional probabilities
        sigma = perplexity ** 0.5
        P = torch.exp(-dist_hd ** 2 / (2 * sigma ** 2))
        P.fill_diagonal_(0)
        P = P / P.sum(dim=1, keepdim=True).clamp(min=1e-10)
        P = (P + P.T) / (2 * N)
        P = P.clamp(min=1e-12)

        dY = torch.zeros_like(Y)

        for it in range(n_iter):
            dist_ld = torch.cdist(Y, Y)
            Q = 1.0 / (1.0 + dist_ld ** 2)
            Q.fill_diagonal_(0)
            Q_norm = Q / Q.sum().clamp(min=1e-10)
            Q_norm = Q_norm.clamp(min=1e-12)

            # Gradient
            PQ_diff = P - Q_norm
            grad = torch.zeros_like(Y)
            for i in range(N):
                diff = Y[i] - Y
                grad[i] = 4.0 * (PQ_diff[i].unsqueeze(1) * Q[i].unsqueeze(1) * diff).sum(dim=0)

            dY = momentum * dY - lr * grad
            Y = Y + dY

        return Y

    @staticmethod
    def plot_embeddings(
        coords_2d: Tensor,
        labels: Optional[Tensor] = None,
        label_names: Optional[Dict[int, str]] = None,
        title: str = "Concept Embedding Space",
        output_path: str = "figures/embedding_space.png",
    ) -> None:
        """Plot 2D embeddings with optional cluster coloring."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig, ax = plt.subplots(figsize=(10, 8))

        x = coords_2d[:, 0].numpy()
        y = coords_2d[:, 1].numpy()

        if labels is not None:
            unique = labels.unique()
            cmap = plt.cm.get_cmap("tab20", len(unique))
            for i, lab in enumerate(unique):
                mask = (labels == lab).numpy()
                name = label_names.get(lab.item(), f"Cluster {lab.item()}") if label_names else f"C{lab.item()}"
                ax.scatter(x[mask], y[mask], c=[cmap(i)], label=name, s=30, alpha=0.7, edgecolors="white", linewidth=0.5)
            ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=8, ncol=2)
        else:
            ax.scatter(x, y, c="#2196F3", s=30, alpha=0.7, edgecolors="white", linewidth=0.5)

        ax.set_title(title, fontsize=14, fontweight="bold")
        ax.set_xlabel("Dimension 1")
        ax.set_ylabel("Dimension 2")
        ax.grid(alpha=0.2)

        plt.tight_layout()
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Figure saved -> {output_path}")


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------

@dataclass
class EmbeddingEvalResult:
    """Results of embedding quality evaluation."""
    mrr: float
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    ndcg_at_10: float
    silhouette: float
    ari: float
    n_samples: int


class EmbeddingEvaluator:
    """Orchestrates all embedding quality evaluations.

    Usage::

        evaluator = EmbeddingEvaluator()
        result = evaluator.evaluate(embeddings, labels, queries, relevance)
        evaluator.visualize(embeddings, labels)
    """

    def __init__(self) -> None:
        self.retrieval = RetrievalMetrics()
        self.clustering = ClusteringMetrics()
        self.visualizer = EmbeddingVisualizer()

    def evaluate(
        self,
        embeddings: Tensor,
        labels: Tensor,
        queries: Optional[Tensor] = None,
        relevance: Optional[Tensor] = None,
    ) -> EmbeddingEvalResult:
        """Run all evaluation metrics.

        Args:
            embeddings: (N, D) corpus embeddings.
            labels: (N,) ground-truth cluster labels.
            queries: Optional (Q, D) query embeddings. If None, uses embeddings.
            relevance: Optional (Q, N) relevance matrix. If None, builds from labels.

        Returns:
            EmbeddingEvalResult with all metrics.
        """
        N = embeddings.size(0)

        # Default: each embedding is its own query, relevant to same-label items
        if queries is None:
            queries = embeddings
        if relevance is None:
            relevance = (labels.unsqueeze(0) == labels.unsqueeze(1)).float()

        flat_emb = embeddings.reshape(N, -1).float()
        flat_q = queries.reshape(queries.size(0), -1).float()

        # Retrieval metrics
        mrr = self.retrieval.mrr(flat_q, flat_emb, relevance)
        r1 = self.retrieval.recall_at_k(flat_q, flat_emb, relevance, k=1)
        r5 = self.retrieval.recall_at_k(flat_q, flat_emb, relevance, k=5)
        r10 = self.retrieval.recall_at_k(flat_q, flat_emb, relevance, k=10)
        ndcg = self.retrieval.ndcg(flat_q, flat_emb, relevance, k=10)

        # Clustering metrics
        sil = self.clustering.silhouette_score(flat_emb, labels)

        # Unsupervised KMeans clustering (no label leakage)
        n_clusters = int(labels.unique().numel())
        # Initialize centroids via K-Means++: first centroid random, rest max-distance
        indices = [torch.randint(0, flat_emb.size(0), (1,)).item()]
        centroids_list = [flat_emb[indices[0]]]
        for _ in range(1, n_clusters):
            dists_sq = torch.cdist(flat_emb, torch.stack(centroids_list)).min(dim=1).values ** 2
            probs = dists_sq / dists_sq.sum().clamp(min=1e-10)
            idx = torch.multinomial(probs, 1).item()
            indices.append(idx)
            centroids_list.append(flat_emb[idx])
        centroids = torch.stack(centroids_list)

        # Run KMeans for 20 iterations
        for _ in range(20):
            dists_to_centroids = torch.cdist(flat_emb, centroids)
            assignments = dists_to_centroids.argmin(dim=1)
            new_centroids = []
            for k in range(n_clusters):
                members = flat_emb[assignments == k]
                if members.numel() > 0:
                    new_centroids.append(members.mean(dim=0))
                else:
                    new_centroids.append(centroids[k])
            centroids = torch.stack(new_centroids)

        pred_labels = torch.cdist(flat_emb, centroids).argmin(dim=1)
        ari = self.clustering.adjusted_rand_index(labels, pred_labels)

        return EmbeddingEvalResult(
            mrr=mrr, recall_at_1=r1, recall_at_5=r5, recall_at_10=r10,
            ndcg_at_10=ndcg, silhouette=sil, ari=ari, n_samples=N,
        )

    def visualize(
        self,
        embeddings: Tensor,
        labels: Optional[Tensor] = None,
        output_dir: str = "figures",
    ) -> None:
        """Generate t-SNE visualization of the embedding space."""
        flat = embeddings.reshape(embeddings.size(0), -1).float()

        # Subsample for speed
        max_n = 500
        if flat.size(0) > max_n:
            idx = torch.randperm(flat.size(0))[:max_n]
            flat = flat[idx]
            if labels is not None:
                labels = labels[idx]

        coords = self.visualizer.tsne_2d(flat, labels, n_iter=300)
        self.visualizer.plot_embeddings(
            coords, labels,
            title="ACRE Concept Embedding Space (t-SNE)",
            output_path=os.path.join(output_dir, "embedding_space.png"),
        )


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("ACRE — Embedding Evaluation Demo")
    print("=" * 60)

    # Generate synthetic embeddings with cluster structure
    n_clusters = 8
    n_per_cluster = 30
    N = n_clusters * n_per_cluster
    d = NUM_ELEMENTS * ELEMENT_DIM

    embeddings = []
    labels_list = []
    for c in range(n_clusters):
        centroid = torch.randn(d) * 3
        cluster_pts = centroid.unsqueeze(0) + torch.randn(n_per_cluster, d) * 0.5
        embeddings.append(cluster_pts)
        labels_list.extend([c] * n_per_cluster)

    embeddings_t = torch.cat(embeddings)
    labels_t = torch.tensor(labels_list)

    evaluator = EmbeddingEvaluator()
    result = evaluator.evaluate(embeddings_t, labels_t)

    print(f"\n--- Retrieval Metrics ---")
    print(f"  MRR:        {result.mrr:.4f}")
    print(f"  Recall@1:   {result.recall_at_1:.4f}")
    print(f"  Recall@5:   {result.recall_at_5:.4f}")
    print(f"  Recall@10:  {result.recall_at_10:.4f}")
    print(f"  nDCG@10:    {result.ndcg_at_10:.4f}")
    print(f"\n--- Clustering Metrics ---")
    print(f"  Silhouette: {result.silhouette:.4f}")
    print(f"  ARI:        {result.ari:.4f}")

    # Generate visualization
    evaluator.visualize(embeddings_t, labels_t)

    print("\nDone [OK]")
