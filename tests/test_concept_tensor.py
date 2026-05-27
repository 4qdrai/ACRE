"""
Tests for ConceptTensor — the 10-element knowledge representation.
"""

import pytest
import torch
import json
from acre.core.concept_tensor import ConceptTensor

class TestConceptTensorCreation:
    """Tests for creating ConceptTensor objects."""

    def test_creation_random(self):
        """ConceptTensor.random() should produce valid random elements."""
        ct = ConceptTensor.random(d=32)
        tensor = ct.to_tensor()
        assert tensor.shape == (10, 32)
        assert tensor.dtype == torch.float32

    def test_creation_zeros(self):
        """ConceptTensor.zeros() should produce valid zero elements."""
        ct = ConceptTensor.zeros(d=32)
        tensor = ct.to_tensor()
        assert tensor.shape == (10, 32)
        assert (tensor == 0.0).all()

    def test_creation_with_elements(self):
        """Creating with explicit element fields should store all 10 elements."""
        d = 16
        elements = {
            name: torch.randn(d) for name in ConceptTensor.ELEMENT_NAMES
        }
        ct = ConceptTensor(**elements)
        for name in ConceptTensor.ELEMENT_NAMES:
            assert torch.allclose(ct[name], elements[name])
            assert torch.allclose(ct.get_element_by_name(name), elements[name])

    def test_creation_custom_dimension(self):
        """Different d values should all work."""
        for d in [8, 32, 128]:
            ct = ConceptTensor.random(d=d)
            assert ct.to_tensor().shape == (10, d)
            assert ct.dim == d


class TestConceptTensorShape:
    """Tests for tensor shape and conversion."""

    def test_to_tensor_shape(self):
        """to_tensor() must return shape (10, d)."""
        ct = ConceptTensor.random(d=64)
        tensor = ct.to_tensor()
        assert tensor.shape == (10, 64)
        assert tensor.dim() == 2

    def test_to_tensor_contiguous(self):
        """Output tensor should be contiguous in memory."""
        ct = ConceptTensor.random(d=64)
        tensor = ct.to_tensor()
        assert tensor.is_contiguous()


class TestConceptTensorRoundtrip:
    """Tests for serialization/deserialization roundtrips."""

    def test_from_tensor_roundtrip(self):
        """create → to_tensor → from_tensor should give back identical concept."""
        original = ConceptTensor.random(d=48)
        tensor = original.to_tensor()
        restored = ConceptTensor.from_tensor(tensor)
        assert original == restored

    def test_to_dict_from_dict(self):
        """to_dict → from_dict should be lossless."""
        original = ConceptTensor.random(d=32)
        d = original.to_dict()
        restored = ConceptTensor.from_dict(d)
        assert original == restored


class TestConceptTensorBatch:
    """Tests for batch operations."""

    def test_stack(self):
        """Stacking N concepts should produce (N, 10, d)."""
        d = 32
        concepts = [ConceptTensor.random(d=d) for _ in range(5)]
        batch = ConceptTensor.stack(concepts)
        assert batch.shape == (5, 10, d)

    def test_unstack(self):
        """Unstacking (N, 10, d) batch tensor should return N ConceptTensors."""
        d = 16
        batch_tensor = torch.randn(4, 10, d)
        concepts = ConceptTensor.unstack(batch_tensor)
        assert len(concepts) == 4
        for i, ct in enumerate(concepts):
            assert ct.dim == d
            assert torch.allclose(ct.to_tensor(), batch_tensor[i])

    def test_collate(self):
        """Collating list of concepts returns dict of element batches of shape (B, d)."""
        d = 32
        concepts = [ConceptTensor.random(d=d) for _ in range(3)]
        collated = ConceptTensor.collate(concepts)
        for name in ConceptTensor.ELEMENT_NAMES:
            assert collated[name].shape == (3, d)
            for i in range(3):
                assert torch.allclose(collated[name][i], concepts[i][name])


class TestConceptTensorValidation:
    """Tests for input validation and error handling."""

    def test_dimension_validation_mixed(self):
        """Elements with different dimensions should raise ValueError."""
        elements = {
            name: torch.randn(16 if i != 0 else 32)
            for i, name in enumerate(ConceptTensor.ELEMENT_NAMES)
        }
        with pytest.raises(ValueError, match="same embedding dimension"):
            ConceptTensor(**elements)

    def test_non_1d_element(self):
        """2-D element tensors should raise ValueError."""
        elements = {
            name: torch.randn(16 if i != 0 else (2, 16))
            for i, name in enumerate(ConceptTensor.ELEMENT_NAMES)
        }
        with pytest.raises(ValueError, match="must be 1-D"):
            ConceptTensor(**elements)

    def test_from_tensor_wrong_first_dim(self):
        """from_tensor with wrong first dimension should raise ValueError."""
        bad_tensor = torch.randn(7, 32)  # Not 10
        with pytest.raises(ValueError, match="Expected tensor of shape"):
            ConceptTensor.from_tensor(bad_tensor)


class TestConceptTensorElementAccess:
    """Tests for accessing elements by name/index."""

    def test_element_names_complete(self):
        """All 10 canonical element names should be present."""
        assert len(ConceptTensor.ELEMENT_NAMES) == 10
        assert "ontological_scaffolding" in ConceptTensor.ELEMENT_NAMES
        assert "illustrative_code" in ConceptTensor.ELEMENT_NAMES

    def test_getitem_and_setitem(self):
        """__getitem__ and __setitem__ should work by name and index."""
        ct = ConceptTensor.random(d=16)
        
        # Test __getitem__ by name
        vec_name = ct["axiomatic_base"]
        assert vec_name.shape == (16,)
        
        # Test __getitem__ by index
        vec_idx = ct[2]  # axiomatic_base is index 2
        assert torch.allclose(vec_name, vec_idx)
        
        # Test __setitem__ by name
        new_vec = torch.ones(16)
        ct["axiomatic_base"] = new_vec
        assert torch.allclose(ct["axiomatic_base"], new_vec)

    def test_setitem_unknown_raises(self):
        """Setting an unknown element should raise ValueError."""
        ct = ConceptTensor.random(d=16)
        with pytest.raises(ValueError, match="Unknown element"):
            ct["nonexistent"] = torch.ones(16)
