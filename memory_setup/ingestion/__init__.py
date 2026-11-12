"""Universal ingestion, magic-byte sniffing, companion pairing, and two-folder routing."""

from memory_setup.ingestion.sniffer import sniff_file, sha256_file
from memory_setup.ingestion.companions import group_companions
from memory_setup.ingestion.router import (
    run_stage1_consolidation,
    run_stage2_standardization,
    run_stage3_validation,
)

__all__ = [
    "sniff_file",
    "sha256_file",
    "group_companions",
    "run_stage1_consolidation",
    "run_stage2_standardization",
    "run_stage3_validation",
]
