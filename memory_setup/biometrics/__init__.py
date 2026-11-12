"""Biometric face detection, ArcFace embedding extraction, and BLAS clustering."""

from memory_setup.biometrics.detector import run_biometric_face_scan
from memory_setup.biometrics.cluster import cluster_embeddings, cluster_and_generate_avatars

__all__ = [
    "run_biometric_face_scan",
    "cluster_embeddings",
    "cluster_and_generate_avatars",
]
