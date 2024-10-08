"""Tests for BaseCollectionEstimator."""

import numpy as np
import pytest

from aeon.base import BaseCollectionEstimator
from aeon.testing.testing_data import (
    EQUAL_LENGTH_UNIVARIATE_CLASSIFICATION,
    UNEQUAL_LENGTH_UNIVARIATE_CLASSIFICATION,
)
from aeon.utils import COLLECTIONS_DATA_TYPES
from aeon.utils.validation import get_type


@pytest.mark.parametrize("data", COLLECTIONS_DATA_TYPES)
def test__get_metadata(data):
    """Test get meta data."""
    X = EQUAL_LENGTH_UNIVARIATE_CLASSIFICATION[data]["train"][0]
    meta = BaseCollectionEstimator._get_X_metadata(X)
    assert not meta["multivariate"]
    assert not meta["missing_values"]
    assert not meta["unequal_length"]
    assert meta["n_channels"] == 1
    assert meta["n_timepoints"] == 20
    assert meta["n_cases"] == 10


def test__check_X():
    """Test if capabilities correctly tested."""
    dummy1 = BaseCollectionEstimator()
    dummy2 = BaseCollectionEstimator()
    all_tags = {
        "capability:multivariate": True,
        "capability:unequal_length": True,
        "capability:missing_values": True,
    }
    dummy2.set_tags(**all_tags)
    X = np.random.random(size=(5, 1, 10))
    assert dummy1._check_X(X) and dummy2._check_X(X)
    X[3][0][6] = np.NAN
    assert dummy2._check_X(X)
    with pytest.raises(ValueError, match=r"cannot handle missing values"):
        dummy1._check_X(X)
    X = np.random.random(size=(5, 3, 10))
    assert dummy2._check_X(X)
    with pytest.raises(ValueError, match=r"cannot handle multivariate"):
        dummy1._check_X(X)
    X[2][2][6] = np.NAN
    assert dummy2._check_X(X)
    with pytest.raises(
        ValueError, match=r"cannot handle missing values or multivariate"
    ):
        dummy1._check_X(X)
    X = [np.random.random(size=(1, 10)), np.random.random(size=(1, 20))]
    assert dummy2._check_X(X)
    with pytest.raises(ValueError, match=r"cannot handle unequal length series"):
        dummy1._check_X(X)
    X = ["Does", "Not", "Accept", "List", "of", "String"]
    with pytest.raises(TypeError, match=r"passed a list containing <class 'str'>"):
        dummy1._check_X(X)


@pytest.mark.parametrize("internal_type", COLLECTIONS_DATA_TYPES)
@pytest.mark.parametrize("data", COLLECTIONS_DATA_TYPES)
def test__convert_X(internal_type, data):
    """Test conversion function.

    The conversion functionality of convertCollection is tested in the utils module.
    This test runs a subset of these but also checks classifiers with multiple
    internal types.
    """
    cls = BaseCollectionEstimator()
    # Equal length should default to numpy3D
    X = EQUAL_LENGTH_UNIVARIATE_CLASSIFICATION[data]["train"][0]
    cls.metadata_ = cls._check_X(X)
    X2 = cls._convert_X(X)
    assert get_type(X2) == cls.get_tag("X_inner_type")
    # should all convert to numpy3D
    cls.set_tags(**{"X_inner_type": "numpy3D"})
    X2 = cls._convert_X(X)
    assert get_type(X2) == "numpy3D"
    # Same as above but as list
    cls.set_tags(**{"X_inner_type": ["numpy3D"]})
    X2 = cls._convert_X(X)
    assert get_type(X2) == "numpy3D"
    # Set cls inner type to just internal_type, should convert to internal_type
    cls.set_tags(**{"X_inner_type": internal_type})
    X2 = cls._convert_X(X)
    assert get_type(X2) == internal_type
    # Should not convert, as datatype is already present
    cls.set_tags(**{"X_inner_type": ["numpy3D", data]})
    X2 = cls._convert_X(X)
    assert get_type(X2) == data
    # Should always convert to numpy3D unless data is already internal_type, as it is
    # the highest priority type
    cls.set_tags(**{"X_inner_type": ["numpy3D", internal_type]})
    X2 = cls._convert_X(X)
    assert get_type(X2) == "numpy3D" if data != internal_type else internal_type

    if data in UNEQUAL_LENGTH_UNIVARIATE_CLASSIFICATION.keys():
        if internal_type in UNEQUAL_LENGTH_UNIVARIATE_CLASSIFICATION.keys():
            X = UNEQUAL_LENGTH_UNIVARIATE_CLASSIFICATION[data]["train"][0]

            # Should stay as internal_type
            cls.set_tags(**{"capability:unequal_length": True})
            cls.set_tags(**{"X_inner_type": ["np-list", data]})
            X2 = cls._convert_X(X)
            assert get_type(X2) == data

            # np-list is the highest priority type for unequal length
            cls.set_tags(**{"capability:unequal_length": True})
            cls.set_tags(**{"X_inner_type": [internal_type, "np-list"]})
            X2 = cls._convert_X(X)
            assert get_type(X2) == "np-list" if data != internal_type else internal_type


@pytest.mark.parametrize("data", COLLECTIONS_DATA_TYPES)
def test_preprocess_collection(data):
    """Test the functionality for preprocessing fit."""
    data = EQUAL_LENGTH_UNIVARIATE_CLASSIFICATION[data]["train"][0]
    cls = BaseCollectionEstimator()
    X = cls._preprocess_collection(data)
    assert cls._n_jobs == 1
    assert len(cls.metadata_) == 6
    assert get_type(X) == "numpy3D"
    tags = {"capability:multithreading": True}
    cls = BaseCollectionEstimator()
    cls.set_tags(**tags)
    with pytest.raises(AttributeError, match="self.n_jobs must be set"):
        cls._preprocess_collection(data)
    # Test two calls do not overwrite metadata (predict should not reset fit meta)
    cls = BaseCollectionEstimator()
    cls._preprocess_collection(data)
    meta = cls.metadata_
    d2 = np.random.random(size=(11, 1, 30))
    cls._preprocess_collection(d2)
    assert meta == cls.metadata_
