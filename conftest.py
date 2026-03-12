"""
pytest configuration for InstaFlow tests.
"""

import asyncio
import pytest




@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for all async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


