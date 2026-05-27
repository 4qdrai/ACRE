"""
ACRE Training Pipeline
======================

Self-supervised training modules for the Algebraic Concept Reasoning Engine.

Modules:
    - concept_distillation: Extract structured 10-element concepts from raw text
    - contrastive_pretraining: InfoNCE contrastive learning for concept embeddings
    - algebraic_pretraining: Self-supervised algebra consistency training
    - self_learning: Self-learning loop with Latent RAG
    - curriculum: 3-phase curriculum scheduler
    - train: Unified training entry point
"""

from acre.training.concept_distillation import TextToConceptPipeline
from acre.training.contrastive_pretraining import ConceptContrastiveTrainer
from acre.training.algebraic_pretraining import AlgebraicPretrainer
from acre.training.self_learning import SelfLearningLoop
from acre.training.curriculum import CurriculumScheduler

__all__ = [
    "TextToConceptPipeline",
    "ConceptContrastiveTrainer",
    "AlgebraicPretrainer",
    "SelfLearningLoop",
    "CurriculumScheduler",
]
