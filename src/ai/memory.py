import json
import hashlib
from typing import Any
import diskcache

# Initialize disk cache for AI responses
cache = diskcache.Cache('.ai_cache')

def _generate_key(prompt: str) -> str:
    """Generate a stable hash for a prompt string."""
    return hashlib.sha256(prompt.strip().lower().encode("utf-8")).hexdigest()

def get_cached_response(prompt: str) -> Any | None:
    """Retrieve a response from cache if it exists."""
    key = _generate_key(prompt)
    if key in cache:
        return cache[key]
    return None

def set_cached_response(prompt: str, response: Any):
    """Save a response to cache."""
    key = _generate_key(prompt)
    cache[key] = response

chat_history = []

def get_chat_history(limit=4) -> list:
    """Return the last N messages of context."""
    return chat_history[-limit:]

def add_chat_history(query: str, answer: str):
    """Add a query and its final answer to the sliding window."""
    chat_history.append({"role": "user", "content": query})
    chat_history.append({"role": "assistant", "content": answer})
    if len(chat_history) > 20:
        chat_history.pop(0)
        chat_history.pop(0)

def clear_cache():
    """Clear all cached responses and chat history."""
    cache.clear()
    chat_history.clear()
