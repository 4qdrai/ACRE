"""
Concept Tensor — The 10-Element Knowledge Operand
==================================================

In the F-LACA architecture, a **Concept** is the atomic unit of structured
knowledge. Unlike flat word embeddings, a Concept Tensor is a *partitioned
manifold* :math:`\\mathbf{c} \\in \\mathbb{R}^{10 \\times d}` where each of the
10 rows encodes a semantically distinct aspect of the concept:

.. math::

    \\mathbf{c} = \\begin{bmatrix}
        c_{\\text{ontology}} \\\\
        c_{\\text{abstraction}} \\\\
        c_{\\text{axioms}} \\\\
        c_{\\text{relations}} \\\\
        c_{\\text{inference}} \\\\
        c_{\\text{methods}} \\\\
        c_{\\text{code}} \\\\
        c_{\\text{goals}} \\\\
        c_{\\text{limits}} \\\\
        c_{\\text{inter\\_concept}}
    \\end{bmatrix}

This explicit partitioning allows the LARE to selectively attend to specific
semantic facets during algebraic reasoning — e.g., the constraint mask Φ
operates exclusively on the ``limitations_risks`` element (index 8).

Design Rationale
----------------
- **Not a flat vector**: Each element has distinct semantics, enabling
  targeted operations (projection, masking, binding).
- **Fixed 10 elements**: Matches the formalized Concept definition from
  the F-LACA hypothesis.
- **Differentiable**: All operations preserve gradients for end-to-end training.
"""

from __future__ import annotations

import torch
from dataclasses import dataclass, fields, field
from typing import Dict, List, Optional, Tuple, ClassVar


# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────
NUM_CONCEPT_ELEMENTS: int = 10
DEFAULT_EMBEDDING_DIM: int = 128

# Canonical ordering of the 10 concept elements
CONCEPT_ELEMENT_NAMES: Tuple[str, ...] = (
    "ontological_scaffolding",
    "abstraction_level",
    "axiomatic_base",
    "relational_network",
    "inferential_framework",
    "methodological_apparatus",
    "illustrative_code",
    "goal_orientation",
    "limitations_risks",
    "inter_concept_relations",
)

# Human-readable descriptions for each element
CONCEPT_ELEMENT_DESCRIPTIONS: Dict[str, str] = {
    "ontological_scaffolding": "Taxonomic position within the knowledge hierarchy",
    "abstraction_level": "Degree of abstraction (concrete ↔ abstract)",
    "axiomatic_base": "Foundational axioms and assumptions the concept rests on",
    "relational_network": "SysML-compliant structural relations to other entities",
    "inferential_framework": "Rules of inference enabled by this concept",
    "methodological_apparatus": "Methods and procedures associated with the concept",
    "illustrative_code": "Executable code stubs illustrating the concept",
    "goal_orientation": "What problems this concept is designed to address",
    "limitations_risks": "Known limitations, failure modes, and risks",
    "inter_concept_relations": "Typed edges to other concepts (uses, extends, contradicts, …)",
}


@dataclass
class ConceptTensor:
    """A 10-element structured concept representation.

    Each field is a ``torch.Tensor`` of shape ``(d,)`` where *d* is the
    embedding dimension (default 128). Together, the 10 fields form a
    partitioned manifold :math:`\\mathbf{c} \\in \\mathbb{R}^{10 \\times d}`.

    Parameters
    ----------
    ontological_scaffolding : torch.Tensor
        Taxonomic position within the knowledge hierarchy.
    abstraction_level : torch.Tensor
        Degree of abstraction (concrete ↔ abstract).
    axiomatic_base : torch.Tensor
        Foundational axioms and assumptions.
    relational_network : torch.Tensor
        SysML-compliant structural relations.
    inferential_framework : torch.Tensor
        Rules of inference enabled by this concept.
    methodological_apparatus : torch.Tensor
        Methods and procedures associated.
    illustrative_code : torch.Tensor
        Executable code stub embeddings.
    goal_orientation : torch.Tensor
        Problem-solving orientation vector.
    limitations_risks : torch.Tensor
        Known limitations and failure modes.
    inter_concept_relations : torch.Tensor
        Typed relational edges to other concepts.

    Examples
    --------
    >>> c = ConceptTensor.zeros(d=128)
    >>> c.to_tensor().shape
    torch.Size([10, 128])

    >>> c2 = ConceptTensor.random(d=64, device="cpu")
    >>> assert c2.dim == 64
    """

    # ── The 10 semantic elements ──────────────────────────────────
    ontological_scaffolding: torch.Tensor
    abstraction_level: torch.Tensor
    axiomatic_base: torch.Tensor
    relational_network: torch.Tensor
    inferential_framework: torch.Tensor
    methodological_apparatus: torch.Tensor
    illustrative_code: torch.Tensor
    goal_orientation: torch.Tensor
    limitations_risks: torch.Tensor
    inter_concept_relations: torch.Tensor
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)

    # Class-level metadata
    NUM_ELEMENTS: ClassVar[int] = NUM_CONCEPT_ELEMENTS
    ELEMENT_NAMES: ClassVar[Tuple[str, ...]] = CONCEPT_ELEMENT_NAMES

    # ── Validation ────────────────────────────────────────────────

    def __post_init__(self) -> None:
        """Validate that all elements share the same embedding dimension."""
        dims = {name: getattr(self, name).shape[-1] for name in self.ELEMENT_NAMES}
        unique_dims = set(dims.values())
        if len(unique_dims) != 1:
            raise ValueError(
                f"All concept elements must have the same embedding dimension d. "
                f"Got dimensions: {dims}"
            )
        # Ensure all are 1-D
        for name in self.ELEMENT_NAMES:
            t = getattr(self, name)
            if t.ndim != 1:
                raise ValueError(
                    f"Element '{name}' must be 1-D (shape (d,)), got shape {t.shape}"
                )

    # ── Properties ────────────────────────────────────────────────

    @property
    def dim(self) -> int:
        """Embedding dimension *d* shared by all 10 elements."""
        return self.ontological_scaffolding.shape[-1]

    @property
    def device(self) -> torch.device:
        """Device of the underlying tensors."""
        return self.ontological_scaffolding.device

    @property
    def dtype(self) -> torch.dtype:
        """Data type of the underlying tensors."""
        return self.ontological_scaffolding.dtype

    # ── Tensor Conversion ─────────────────────────────────────────

    def to_tensor(self) -> torch.Tensor:
        """Concatenate all 10 elements into a single ``(10, d)`` tensor.

        Returns
        -------
        torch.Tensor
            Shape ``(10, d)`` — each row is one semantic element, in
            canonical order.
        """
        return torch.stack(
            [getattr(self, name) for name in self.ELEMENT_NAMES], dim=0
        )

    @classmethod
    def from_tensor(cls, tensor: torch.Tensor) -> "ConceptTensor":
        """Reconstruct a ConceptTensor from a ``(10, d)`` tensor.

        Parameters
        ----------
        tensor : torch.Tensor
            Shape ``(10, d)`` in canonical element order.

        Returns
        -------
        ConceptTensor

        Raises
        ------
        ValueError
            If tensor does not have exactly 10 rows.
        """
        if tensor.ndim != 2 or tensor.shape[0] != NUM_CONCEPT_ELEMENTS:
            raise ValueError(
                f"Expected tensor of shape (10, d), got {tensor.shape}"
            )
        elements = {
            name: tensor[i] for i, name in enumerate(CONCEPT_ELEMENT_NAMES)
        }
        return cls(**elements)

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> Dict[str, torch.Tensor]:
        """Serialize to a dictionary of named tensors.

        Useful for ``torch.save`` / ``torch.load`` round-tripping.
        """
        return {name: getattr(self, name).detach().clone() for name in self.ELEMENT_NAMES}

    @classmethod
    def from_dict(cls, data: Dict[str, torch.Tensor]) -> "ConceptTensor":
        """Deserialize from a dictionary of named tensors.

        Parameters
        ----------
        data : dict
            Keys must be the canonical element names.
        """
        missing = set(CONCEPT_ELEMENT_NAMES) - set(data.keys())
        if missing:
            raise KeyError(f"Missing concept elements: {missing}")
        return cls(**{name: data[name] for name in CONCEPT_ELEMENT_NAMES})

    # ── Batch Operations ──────────────────────────────────────────

    @staticmethod
    def stack(concepts: List["ConceptTensor"]) -> torch.Tensor:
        """Stack a list of ConceptTensors into a batch tensor.

        Parameters
        ----------
        concepts : list of ConceptTensor
            All must have the same embedding dimension *d*.

        Returns
        -------
        torch.Tensor
            Shape ``(B, 10, d)`` where ``B = len(concepts)``.
        """
        if not concepts:
            raise ValueError("Cannot stack an empty list of concepts")
        return torch.stack([c.to_tensor() for c in concepts], dim=0)

    @staticmethod
    def unstack(batch_tensor: torch.Tensor) -> List["ConceptTensor"]:
        """Split a ``(B, 10, d)`` batch tensor into individual ConceptTensors.

        Parameters
        ----------
        batch_tensor : torch.Tensor
            Shape ``(B, 10, d)``.

        Returns
        -------
        list of ConceptTensor
        """
        if batch_tensor.ndim != 3 or batch_tensor.shape[1] != NUM_CONCEPT_ELEMENTS:
            raise ValueError(
                f"Expected shape (B, 10, d), got {batch_tensor.shape}"
            )
        return [ConceptTensor.from_tensor(batch_tensor[i]) for i in range(batch_tensor.shape[0])]

    @staticmethod
    def collate(concepts: List["ConceptTensor"]) -> Dict[str, torch.Tensor]:
        """Collate concepts into a dict of batched tensors.

        This is useful as a custom ``collate_fn`` for PyTorch DataLoaders.

        Returns
        -------
        dict
            Keys are element names, values are tensors of shape ``(B, d)``.
        """
        if not concepts:
            raise ValueError("Cannot collate an empty list")
        return {
            name: torch.stack([getattr(c, name) for c in concepts], dim=0)
            for name in CONCEPT_ELEMENT_NAMES
        }

    # ── Factories ─────────────────────────────────────────────────

    @classmethod
    def zeros(
        cls,
        d: int = DEFAULT_EMBEDDING_DIM,
        device: Optional[torch.device] = None,
        dtype: torch.dtype = torch.float32,
    ) -> "ConceptTensor":
        """Create a zero-initialized ConceptTensor.

        Parameters
        ----------
        d : int
            Embedding dimension (default 128).
        device : torch.device, optional
        dtype : torch.dtype
        """
        return cls(**{
            name: torch.zeros(d, device=device, dtype=dtype)
            for name in CONCEPT_ELEMENT_NAMES
        })

    @classmethod
    def random(
        cls,
        d: int = DEFAULT_EMBEDDING_DIM,
        device: Optional[torch.device] = None,
        dtype: torch.dtype = torch.float32,
    ) -> "ConceptTensor":
        """Create a randomly initialized ConceptTensor (standard normal).

        Useful for testing and initialization.
        """
        return cls(**{
            name: torch.randn(d, device=device, dtype=dtype)
            for name in CONCEPT_ELEMENT_NAMES
        })

    # ── Element Access ────────────────────────────────────────────

    def get_element(self, index: int) -> torch.Tensor:
        """Access an element by its canonical index (0-9).

        Parameters
        ----------
        index : int
            Element index in ``[0, 9]``.

        Returns
        -------
        torch.Tensor
            Shape ``(d,)``.
        """
        if not 0 <= index < self.NUM_ELEMENTS:
            raise IndexError(f"Element index must be in [0, 9], got {index}")
        return getattr(self, self.ELEMENT_NAMES[index])

    def get_element_by_name(self, name: str) -> torch.Tensor:
        """Access an element by its canonical name."""
        if name not in self.ELEMENT_NAMES:
            raise KeyError(
                f"Unknown element '{name}'. Valid: {self.ELEMENT_NAMES}"
            )
        return getattr(self, name)

    # ── Device / dtype movement ───────────────────────────────────

    def to(
        self,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ) -> "ConceptTensor":
        """Move all elements to the specified device / dtype.

        Returns a *new* ConceptTensor (non-mutating).
        """
        kwargs = {}
        if device is not None:
            kwargs["device"] = device
        if dtype is not None:
            kwargs["dtype"] = dtype
        return ConceptTensor(**{
            name: getattr(self, name).to(**kwargs)
            for name in self.ELEMENT_NAMES
        })

    # ── Utility ───────────────────────────────────────────────────

    def norm(self, p: int = 2) -> torch.Tensor:
        """Compute the L-p norm of the full ``(10, d)`` tensor."""
        return self.to_tensor().norm(p=p)

    def cosine_similarity(self, other: "ConceptTensor") -> torch.Tensor:
        """Element-wise cosine similarity with another ConceptTensor.

        Returns
        -------
        torch.Tensor
            Shape ``(10,)`` — one similarity score per element.
        """
        a = self.to_tensor()   # (10, d)
        b = other.to_tensor()  # (10, d)
        return torch.nn.functional.cosine_similarity(a, b, dim=-1)

    def __repr__(self) -> str:
        return (
            f"ConceptTensor(d={self.dim}, device={self.device}, "
            f"dtype={self.dtype})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConceptTensor):
            return NotImplemented
        return torch.allclose(self.to_tensor(), other.to_tensor())

    def __getitem__(self, key: str | int) -> torch.Tensor:
        if isinstance(key, int):
            return self.get_element(key)
        return self.get_element_by_name(key)

    def __setitem__(self, key: str, value: torch.Tensor):
        if key not in self.ELEMENT_NAMES:
            raise ValueError(f"Unknown element '{key}'. Valid: {self.ELEMENT_NAMES}")
        if value.shape != getattr(self, key).shape:
            raise ValueError(f"Shape mismatch: expected {getattr(self, key).shape}, got {value.shape}")
        setattr(self, key, value)

    @property
    def flat(self) -> torch.Tensor:
        """Flattened tensor representation of all elements (10 * d,)."""
        return self.to_tensor().reshape(-1)
