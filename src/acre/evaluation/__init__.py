"""
ACRE Evaluation Suite
=====================

Benchmarks and metrics for measuring ACRE system performance.

Modules:
    - scan_benchmark: SCAN compositional generalization benchmark
    - c2e_metric: C2E Concept Evaluation Metric (10-element weighted scoring)
    - compression_analysis: Compression ratio and FLOP comparison analysis
    - embedding_evaluation: Embedding quality metrics (MRR, Recall@K, clustering)
"""

from acre.evaluation.scan_benchmark import SCANBenchmark
from acre.evaluation.c2e_metric import C2EMetric
from acre.evaluation.compression_analysis import CompressionAnalyzer
from acre.evaluation.embedding_evaluation import EmbeddingEvaluator

__all__ = [
    "SCANBenchmark",
    "C2EMetric",
    "CompressionAnalyzer",
    "EmbeddingEvaluator",
]
