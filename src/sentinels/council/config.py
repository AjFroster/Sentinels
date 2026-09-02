"""Runtime configuration that is not a user preference.

Settings holds what the user chooses; this holds what the environment dictates.
The Ollama host lives here because the end-to-end tests point the whole app at
a stub server, and a value hardcoded in three modules cannot be redirected.
"""

import os

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


def ollama_host() -> str:
    """Where the local model server is listening.

    Loopback by default and deliberately so: binding or dialling anything else
    is the change that would take a sealed question off this machine.
    """
    return os.environ.get("SENTINELS_OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/")
