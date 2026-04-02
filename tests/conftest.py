"""Pytest fixtures for INTER.act test suite."""

import pytest
from app import create_app
from app.config import TestingConfig
from app.models import arm_state


@pytest.fixture
def app():
    """Create a Flask application instance for testing."""
    application = create_app(config_class=TestingConfig)
    yield application


@pytest.fixture
def client(app):
    """Create a Flask test client."""
    return app.test_client()


@pytest.fixture(autouse=True)
def reset_arm_state():
    """Reset arm state before each test to ensure isolation."""
    arm_state.reset()
    yield
    arm_state.reset()
