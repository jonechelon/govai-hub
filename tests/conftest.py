"""
conftest.py

Pytest configuration and shared fixtures.
Sets dummy GROQ_API_KEY when missing so integration tests can run without .env.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def ensure_groq_key_for_imports():
    """Ensure GROQ_API_KEY is set before any module imports GroqClient (e.g. digest_generator)."""
    if not os.environ.get("GROQ_API_KEY"):
        os.environ["GROQ_API_KEY"] = "test-key-for-pytest"
