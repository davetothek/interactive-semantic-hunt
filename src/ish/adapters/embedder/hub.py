"""Keep the Hugging Face hub quiet while a backend fetches its model.

A first run downloads a model, and the hub reports telemetry, symlink
and login warnings as it goes. None of that is an answer to a search,
and in a picker it lands on top of the results.
"""

import logging
import os


def quiet_hub() -> None:
    """Turn off the hub's telemetry and its warnings, before it is imported."""
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
