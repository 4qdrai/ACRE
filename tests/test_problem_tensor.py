"""
Tests for ProblemTensor — the 10-element Generalized Problem Formulation (GPF).
"""

import pytest
import torch
import json
from acre.core.problem_tensor import ProblemTensor

class TestProblemTensorCreation:
    """Tests for creating ProblemTensor objects."""

    def test_creation_random(self):
        """ProblemTensor.random() should produce valid random elements."""
        pt = ProblemTensor.random(d=32)
        tensor = pt.to_tensor()
        assert tensor.shape == (10, 32)
        assert tensor.dtype == torch.float32

    def test_creation_zeros(self):
        """ProblemTensor.zeros() should produce valid zero elements."""
        pt = ProblemTensor.zeros(d=32)
        tensor = pt.to_tensor()
        assert tensor.shape == (10, 32)
        assert (tensor == 0.0).all()

    def test_creation_with_elements(self):
        """Creating with explicit elements should store all correctly."""
        d = 24
        elements = {n: torch.randn(d) for n in ProblemTensor.ELEMENT_NAMES}
        pt = ProblemTensor(**elements)
        for name in ProblemTensor.ELEMENT_NAMES:
            assert torch.allclose(pt[name], elements[name])
            assert torch.allclose(pt.get_element_by_name(name), elements[name])

    def test_element_count(self):
        """ProblemTensor must have exactly 10 elements."""
        assert ProblemTensor.NUM_ELEMENTS == 10
        assert len(ProblemTensor.ELEMENT_NAMES) == 10


class TestProblemTensorShape:
    """Tests for tensor shape."""

    def test_to_tensor_shape(self):
        """to_tensor() must return (10, d)."""
        for d in [16, 64]:
            pt = ProblemTensor.random(d=d)
            assert pt.to_tensor().shape == (10, d)
            assert pt.dim == d

    def test_to_tensor_dtype(self):
        """Output should be float32."""
        pt = ProblemTensor.random(d=32)
        assert pt.to_tensor().dtype == torch.float32


class TestProblemTensorSpecialElements:
    """Tests for the GPF-specific accessors (constraint vector, verification stub, requirements, specification)."""

    def test_constraint_vector(self):
        """get_constraint_vector() should return constraints_context exactly."""
        d = 32
        pt = ProblemTensor.random(d=d)
        assert torch.allclose(pt.get_constraint_vector(), pt.constraints_context)
        # Element 6 = index 5
        assert torch.allclose(pt.to_tensor()[5], pt.get_constraint_vector())

    def test_verification_stub(self):
        """get_verification_stub() should return verification_code exactly."""
        d = 32
        pt = ProblemTensor.random(d=d)
        assert torch.allclose(pt.get_verification_stub(), pt.verification_code)
        # Element 5 = index 4
        assert torch.allclose(pt.to_tensor()[4], pt.get_verification_stub())

    def test_formal_requirements(self):
        """get_formal_requirements() should return formal_requirements exactly."""
        d = 32
        pt = ProblemTensor.random(d=d)
        assert torch.allclose(pt.get_formal_requirements(), pt.formal_requirements)
        # Element 3 = index 2
        assert torch.allclose(pt.to_tensor()[2], pt.get_formal_requirements())

    def test_formal_specification(self):
        """get_formal_specification() should return formal_specification exactly."""
        d = 32
        pt = ProblemTensor.random(d=d)
        assert torch.allclose(pt.get_formal_specification(), pt.formal_specification)
        # Element 4 = index 3
        assert torch.allclose(pt.to_tensor()[3], pt.get_formal_specification())


class TestProblemTensorSerialization:
    """Tests for serialization roundtrips."""

    def test_serialization_roundtrip(self):
        """to_dict → from_dict should be lossless."""
        original = ProblemTensor.random(d=24)
        d = original.to_dict()
        restored = ProblemTensor.from_dict(d)
        assert original == restored


class TestProblemTensorBatch:
    """Tests for batch operations."""

    def test_stack(self):
        """Stacking N problems should produce (N, 10, d)."""
        d = 32
        problems = [ProblemTensor.random(d=d) for _ in range(8)]
        batch = ProblemTensor.stack(problems)
        assert batch.shape == (8, 10, d)

    def test_unstack(self):
        """Unstacking N problems should restore them correctly."""
        d = 16
        batch_tensor = torch.randn(6, 10, d)
        problems = ProblemTensor.unstack(batch_tensor)
        assert len(problems) == 6
        for i, pt in enumerate(problems):
            assert torch.allclose(pt.to_tensor(), batch_tensor[i])

    def test_collate(self):
        """Collating list of problems returns dict of element batches of shape (B, d)."""
        d = 32
        problems = [ProblemTensor.random(d=d) for _ in range(3)]
        collated = ProblemTensor.collate(problems)
        for name in ProblemTensor.ELEMENT_NAMES:
            assert collated[name].shape == (3, d)
            for i in range(3):
                assert torch.allclose(collated[name][i], problems[i][name])


class TestProblemTensorElementAccess:
    """Tests for element access by name/index."""

    def test_getitem_and_setitem(self):
        """__getitem__ and __setitem__ should work by name and index."""
        pt = ProblemTensor.random(d=16)
        
        # Test __getitem__ by name
        vec_name = pt["constraints_context"]
        assert vec_name.shape == (16,)
        
        # Test __getitem__ by index
        vec_idx = pt[5]  # constraints_context is index 5
        assert torch.allclose(vec_name, vec_idx)
        
        # Test __setitem__ by name
        new_vec = torch.ones(16)
        pt["constraints_context"] = new_vec
        assert torch.allclose(pt["constraints_context"], new_vec)
