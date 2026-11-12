"""High-precision document classifier and memory-bounded local VLM vision client."""

from memory_setup.vision.classifier import is_homework_or_document, classify_description
from memory_setup.vision.ollama_client import MemoryBoundedOllamaClient, run_vision_pipeline

__all__ = [
    "is_homework_or_document",
    "classify_description",
    "MemoryBoundedOllamaClient",
    "run_vision_pipeline",
]
