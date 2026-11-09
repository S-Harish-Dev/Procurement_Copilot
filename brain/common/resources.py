"""Size process pools by free memory, not core count so parallel jobs never exhaust RAM on a shared machine.

Measured peak RSS per worker (2026-10-30): docling ~2.5 GB, spaCy-lg+Presidio ~1.5 GB,
nomic ONNX embedding ~3.7 GB. A pool may use at most MEMORY_SHARE of MemAvailable
at start-up, so a big run never pushes other users' jobs into the OOM killer.
"""

MEMORY_SHARE = 0.4


def available_gb() -> float:
    with open("/proc/meminfo") as f:
        info = dict(line.split(":", 1) for line in f)
    return int(info["MemAvailable"].split()[0]) / 1e6


def capped_workers(requested: int, gb_per_worker: float) -> int:
    return max(1, min(requested, int(available_gb() * MEMORY_SHARE / gb_per_worker)))
