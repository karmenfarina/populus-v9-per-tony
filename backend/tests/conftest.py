import os
import pytest
from dotenv import load_dotenv

# Load backend/.env so MONGO_URL / DB_NAME / EMERGENT_LLM_KEY are available
load_dotenv("/app/backend/.env")


def pytest_collection_modifyitems(config, items):
    # Ensure asyncio tests run in the default loop scope
    pass
