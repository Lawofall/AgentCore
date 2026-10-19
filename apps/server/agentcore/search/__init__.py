"""Account search wiring: chosen engine + connection settings.

Leaf HTTP (DeepSeek POST, SearXNG, ``web_search``) stays in
``tools/builtin/web``. This package owns the account slot, decrypts the
official DeepSeek BYOK key via the LLM credential bridge, and records
reconnect state.
"""
