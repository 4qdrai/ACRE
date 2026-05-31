"""
Problem Tensor — The 10-Element GPF (Generalized Problem Formulation)
=====================================================================

In the ACRE architecture, a **Problem** (GPF) is the task operator that
drives algebraic reasoning over Concept operands. Like the Concept Tensor,
the Problem Tensor is a partitioned manifold
:math:`\\mathbf{p} \\in \\mathbb{R}^{10 \\times d}`, but its 10 elements
encode *task-specific* semantics:

.. math::

    \\mathbf{p} = \\begin{bmatrix}
        p_{\\text{core\\_def}} \\\\
        p_{\\text{architecture}} \\\\
        p_{\\text{formal\\_reqs}} \\\\
        p_{\\text{formal\\_spec}} \\\\
        p_{\\text{verification}} \\\\
        p_{\\text{constraints}} \\\\
        p_{\\text{methods}} \\\\
        p_{\\text{metrics}} \\\\
        p_{\\text{scope}} \\\\
        p_{\\text{related}}
    \\end{bmatrix}

Key Roles in LARE
-----------------
- **Element 2 (formal_requirements)**: The :math:`p_{formal\\_reqs}` vector
  is used by LARE to dynamically weight algebraic operators via
  :math:`\\sigma(W_m \\cdot p_{formal\\_reqs})`.
- **Element 5 (constraints_context)**: Fed into the constraint mask Φ
  to gate invalid states: :math:`\\Phi(p_{constraints}, c_{limitations})`.
- **Element 4 (verification_code)**: Provides executable verification
  logic for formal solution validation.
"""

from __future__ import annotations

import torch
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, ClassVar, Any

from acre.core.concept_tensor import DEFAULT_EMBEDDING_DIM


# ────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────
NUM_PROBLEM_ELEMENTS: int = 10

PROBLEM_ELEMENT_NAMES: Tuple[str, ...] = (
    "core_definition",
    "architecture",
    "formal_requirements",
    "formal_specification",
    "verification_code",
    "constraints_context",
    "methods",
    "metrics",
    "scope",
    "related_problems",
)

PROBLEM_ELEMENT_DESCRIPTIONS: Dict[str, str] = {
    "core_definition": "The essential statement of what the problem asks",
    "architecture": "System architecture context the problem lives in",
    "formal_requirements": "Formal requirements that any solution must satisfy",
    "formal_specification": "Mathematical / logical specification of correctness",
    "verification_code": "Executable Python stubs for verifying solutions",
    "constraints_context": "Operational constraints and contextual bounds",
    "methods": "Permitted or suggested solution methods",
    "metrics": "Quantitative evaluation metrics for solution quality",
    "scope": "Boundaries of what is in-scope vs. out-of-scope",
    "related_problems": "Typed relations to other GPFs (subsumes, extends, …)",
}

# Special element indices with semantic roles in the LARE pipeline
IDX_FORMAL_REQUIREMENTS: int = 2   # Used for operator gating
IDX_FORMAL_SPECIFICATION: int = 3  # Used for verification
IDX_VERIFICATION_CODE: int = 4     # Executable verification stubs
IDX_CONSTRAINTS_CONTEXT: int = 5   # Fed into Φ mask


@dataclass
class ProblemTensor:
    """A 10-element structured problem (GPF) representation.

    Each field is a ``torch.Tensor`` of shape ``(d,)`` where *d* is the
    embedding dimension. Together, the 10 fields form the GPF manifold
    :math:`\\mathbf{p} \\in \\mathbb{R}^{10 \\times d}`.

    The ProblemTensor serves as the **operator** in the LARE equation:

    .. math::

        c_{out}^{(t)} = \\sum_i \\sum_j \\alpha_{ij}
        \\left( \\sum_m \\sigma(W_m \\, p_{i, \\text{formal\\_reqs}})
        \\mathcal{O}_m(c_j, c_{ctx}) \\right)
        \\cdot \\Phi(p_{i, \\text{constraints}}, c_{j, \\text{limits}})

    Parameters
    ----------
    core_definition : torch.Tensor
        Essential problem statement embedding.
    architecture : torch.Tensor
        System architecture context.
    formal_requirements : torch.Tensor
        Formal requirements for operator gating.
    formal_specification : torch.Tensor
        Mathematical specification of correctness.
    verification_code : torch.Tensor
        Executable verification stub embedding.
    constraints_context : torch.Tensor
        Operational constraints for Φ mask.
    methods : torch.Tensor
        Permitted solution methods.
    metrics : torch.Tensor
        Evaluation metrics.
    scope : torch.Tensor
        Problem boundaries.
    related_problems : torch.Tensor
        Relations to other GPFs.

    Examples
    --------
    >>> p = ProblemTensor.zeros(d=128)
    >>> p.get_constraint_vector().shape
    torch.Size([128])
    """

    # ── The 10 semantic elements ──────────────────────────────────
    core_definition: torch.Tensor
    architecture: torch.Tensor
    formal_requirements: torch.Tensor
    formal_specification: torch.Tensor
    verification_code: torch.Tensor
    constraints_context: torch.Tensor
    methods: torch.Tensor
    metrics: torch.Tensor
    scope: torch.Tensor
    related_problems: torch.Tensor
    metadata: Dict[str, Any] = field(default_factory=dict, compare=False)

    # Class-level metadata
    NUM_ELEMENTS: ClassVar[int] = NUM_PROBLEM_ELEMENTS
    ELEMENT_NAMES: ClassVar[Tuple[str, ...]] = PROBLEM_ELEMENT_NAMES

    # ── Validation ────────────────────────────────────────────────

    def __post_init__(self) -> None:
        """Validate that all elements share the same embedding dimension."""
        dims = {name: getattr(self, name).shape[-1] for name in self.ELEMENT_NAMES}
        unique_dims = set(dims.values())
        if len(unique_dims) != 1:
            raise ValueError(
                f"All problem elements must have the same embedding dimension d. "
                f"Got dimensions: {dims}"
            )
        for name in self.ELEMENT_NAMES:
            t = getattr(self, name)
            if t.ndim != 1:
                raise ValueError(
                    f"Element '{name}' must be 1-D (shape (d,)), got shape {t.shape}"
                )

    # ── Properties ────────────────────────────────────────────────

    @property
    def dim(self) -> int:
        """Embedding dimension *d*."""
        return self.core_definition.shape[-1]

    @property
    def device(self) -> torch.device:
        return self.core_definition.device

    @property
    def dtype(self) -> torch.dtype:
        return self.core_definition.dtype

    # ── Tensor Conversion ─────────────────────────────────────────

    def to_tensor(self) -> torch.Tensor:
        """Concatenate all 10 elements into a ``(10, d)`` tensor."""
        return torch.stack(
            [getattr(self, name) for name in self.ELEMENT_NAMES], dim=0
        )

    @classmethod
    def from_tensor(cls, tensor: torch.Tensor) -> "ProblemTensor":
        """Reconstruct from a ``(10, d)`` tensor in canonical order.

        Raises
        ------
        ValueError
            If tensor shape is not ``(10, d)``.
        """
        if tensor.ndim != 2 or tensor.shape[0] != NUM_PROBLEM_ELEMENTS:
            raise ValueError(
                f"Expected tensor of shape (10, d), got {tensor.shape}"
            )
        return cls(**{
            name: tensor[i] for i, name in enumerate(PROBLEM_ELEMENT_NAMES)
        })

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> Dict[str, torch.Tensor]:
        """Serialize to a dictionary of named tensors."""
        return {
            name: getattr(self, name).detach().clone()
            for name in self.ELEMENT_NAMES
        }

    @classmethod
    def from_dict(cls, data: Dict[str, torch.Tensor]) -> "ProblemTensor":
        """Deserialize from a dictionary of named tensors."""
        missing = set(PROBLEM_ELEMENT_NAMES) - set(data.keys())
        if missing:
            raise KeyError(f"Missing problem elements: {missing}")
        return cls(**{name: data[name] for name in PROBLEM_ELEMENT_NAMES})

    # ── LARE-Specific Accessors ───────────────────────────────────

    def get_constraint_vector(self) -> torch.Tensor:
        """Return the constraints_context element for the Φ mask.

        This vector is fed into
        :class:`~acre.core.constraint_mask.ConstraintMask` along with
        the Concept's ``limitations_risks`` element to compute the
        geometric gating mask:

        .. math::

            \\Phi(p_{\\text{constraints}}, c_{\\text{limitations}})
            \\in [0, 1]^d

        Returns
        -------
        torch.Tensor
            Shape ``(d,)`` — the constraint context vector.
        """
        return self.constraints_context

    def get_formal_requirements(self) -> torch.Tensor:
        """Return the formal_requirements element for operator gating.

        Used in LARE as :math:`\\sigma(W_m \\cdot p_{formal\\_reqs})`
        to dynamically weight algebraic operators.

        Returns
        -------
        torch.Tensor
            Shape ``(d,)`` — the formal requirements vector.
        """
        return self.formal_requirements

    def get_verification_stub(self) -> torch.Tensor:
        """Return the verification_code element for solution checking.

        This embedding represents executable verification logic (Python
        ABC stubs in the original formulation). The SolutionTensor's
        ``verify()`` method uses this to validate algebraic solutions.

        Returns
        -------
        torch.Tensor
            Shape ``(d,)`` — the verification code embedding.
        """
        return self.verification_code

    def get_formal_specification(self) -> torch.Tensor:
        """Return the formal_specification element.

        Encodes the mathematical / logical correctness specification that
        any valid solution must satisfy.

        Returns
        -------
        torch.Tensor
            Shape ``(d,)`` — the formal specification vector.
        """
        return self.formal_specification

    # ── Batch Operations ──────────────────────────────────────────

    @staticmethod
    def stack(problems: List["ProblemTensor"]) -> torch.Tensor:
        """Stack problems into a ``(B, 10, d)`` batch tensor."""
        if not problems:
            raise ValueError("Cannot stack an empty list of problems")
        return torch.stack([p.to_tensor() for p in problems], dim=0)

    @staticmethod
    def unstack(batch_tensor: torch.Tensor) -> List["ProblemTensor"]:
        """Split a ``(B, 10, d)`` batch tensor into ProblemTensors."""
        if batch_tensor.ndim != 3 or batch_tensor.shape[1] != NUM_PROBLEM_ELEMENTS:
            raise ValueError(
                f"Expected shape (B, 10, d), got {batch_tensor.shape}"
            )
        return [
            ProblemTensor.from_tensor(batch_tensor[i])
            for i in range(batch_tensor.shape[0])
        ]

    @staticmethod
    def collate(problems: List["ProblemTensor"]) -> Dict[str, torch.Tensor]:
        """Collate problems into a dict of batched tensors.

        Returns
        -------
        dict
            Keys are element names, values are tensors of shape ``(B, d)``.
        """
        if not problems:
            raise ValueError("Cannot collate an empty list")
        return {
            name: torch.stack([getattr(p, name) for p in problems], dim=0)
            for name in PROBLEM_ELEMENT_NAMES
        }

    # ── Factories ─────────────────────────────────────────────────

    @classmethod
    def zeros(
        cls,
        d: int = DEFAULT_EMBEDDING_DIM,
        device: Optional[torch.device] = None,
        dtype: torch.dtype = torch.float32,
    ) -> "ProblemTensor":
        """Create a zero-initialized ProblemTensor."""
        return cls(**{
            name: torch.zeros(d, device=device, dtype=dtype)
            for name in PROBLEM_ELEMENT_NAMES
        })

    @classmethod
    def random(
        cls,
        d: int = DEFAULT_EMBEDDING_DIM,
        device: Optional[torch.device] = None,
        dtype: torch.dtype = torch.float32,
    ) -> "ProblemTensor":
        """Create a randomly initialized ProblemTensor (standard normal)."""
        return cls(**{
            name: torch.randn(d, device=device, dtype=dtype)
            for name in PROBLEM_ELEMENT_NAMES
        })

    # ── Device / dtype ────────────────────────────────────────────

    def to(
        self,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ) -> "ProblemTensor":
        """Move all elements to the specified device / dtype."""
        kwargs = {}
        if device is not None:
            kwargs["device"] = device
        if dtype is not None:
            kwargs["dtype"] = dtype
        return ProblemTensor(**{
            name: getattr(self, name).to(**kwargs)
            for name in self.ELEMENT_NAMES
        })

    # ── Element Access ────────────────────────────────────────────

    def get_element(self, index: int) -> torch.Tensor:
        """Access an element by canonical index ``[0, 9]``."""
        if not 0 <= index < self.NUM_ELEMENTS:
            raise IndexError(f"Element index must be in [0, 9], got {index}")
        return getattr(self, self.ELEMENT_NAMES[index])

    def get_element_by_name(self, name: str) -> torch.Tensor:
        """Access an element by canonical name."""
        if name not in self.ELEMENT_NAMES:
            raise KeyError(
                f"Unknown element '{name}'. Valid: {self.ELEMENT_NAMES}"
            )
        return getattr(self, name)

    # ── Utility ───────────────────────────────────────────────────

    def norm(self, p: int = 2) -> torch.Tensor:
        """Compute the L-p norm of the full ``(10, d)`` tensor."""
        return self.to_tensor().norm(p=p)

    def cosine_similarity(self, other: "ProblemTensor") -> torch.Tensor:
        """Element-wise cosine similarity with another ProblemTensor.

        Returns shape ``(10,)`` — one similarity per element.
        """
        a = self.to_tensor()
        b = other.to_tensor()
        return torch.nn.functional.cosine_similarity(a, b, dim=-1)

    def __repr__(self) -> str:
        return (
            f"ProblemTensor(d={self.dim}, device={self.device}, "
            f"dtype={self.dtype})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ProblemTensor):
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
