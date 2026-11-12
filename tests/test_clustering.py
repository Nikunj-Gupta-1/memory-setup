from __future__ import annotations

import numpy as np

from memory_setup.biometrics.cluster import cluster_embeddings


def _unit_vec(rng: np.random.Generator, dim: int) -> np.ndarray:
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


def test_cluster_embeddings_512d_separates_identities() -> None:
    rng = np.random.default_rng(42)
    base_a = _unit_vec(rng, 512)
    base_b = _unit_vec(rng, 512)

    # 5 tight variations of person A, 4 of person B, 1 noise outlier
    embs = []
    for _ in range(5):
        v = base_a + 0.015 * rng.normal(size=512).astype(np.float32)
        embs.append(v / np.linalg.norm(v))
    for _ in range(4):
        v = base_b + 0.015 * rng.normal(size=512).astype(np.float32)
        embs.append(v / np.linalg.norm(v))
    embs.append(_unit_vec(rng, 512))

    mat = np.stack(embs)
    labels, stats = cluster_embeddings(mat, thresh=0.48, min_cluster_size=3)

    assert stats["recurring_clusters"] == 2
    assert len(set(labels[:5])) == 1 and labels[0] >= 0
    assert len(set(labels[5:9])) == 1 and labels[5] >= 0
    assert labels[0] != labels[5]
    assert labels[9] == -1  # single outlier < min_cluster_size


def test_cluster_embeddings_exceeds_15000_centroids_without_overflow() -> None:
    # Stress-test dynamic centroid buffer doubling past the old 15,000 hard limit
    n_vecs = 16500
    dim = 16
    # Deterministic near-orthogonal normalized hash patterns via angles / pseudo-random matrix
    rng = np.random.default_rng(7)
    mat = rng.normal(size=(n_vecs, dim)).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True)

    # Very high similarity threshold guarantees most vectors open a new centroid (> 15,000 centroids)
    labels, stats = cluster_embeddings(mat, thresh=0.9999, min_cluster_size=2)
    assert len(labels) == n_vecs
    assert stats["num_raw_clusters"] > 15000
