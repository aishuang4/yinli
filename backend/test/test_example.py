# test_example.py
import pytest


def test_always_passes():
    assert True


def test_fastapi_import():
    try:
        import fastapi
        assert hasattr(fastapi, "FastAPI")
    except ImportError:
        pytest.skip("fastapi not available")
