from __future__ import annotations

import math
import random
from collections import defaultdict

from databridge.export.models import SamplingConfig, SamplingMethod


class SamplingBuffer:
    def __init__(self, config: SamplingConfig) -> None:
        self._method = config.method
        self._target_column = config.target_column
        self._ratio_or_size = config.ratio_or_size
        self._seen = 0
        self._kept = 0
        # For systematic: counter
        self._sys_step: float = (1.0 / config.ratio_or_size) if config.ratio_or_size < 1.0 else 1.0
        self._sys_next: float = 0.0
        # For stratified: track per-group
        self._strat_counts: dict[str, int] = defaultdict(int)
        self._strat_kept: dict[str, int] = defaultdict(int)

    def feed(self, record: dict) -> bool:
        self._seen += 1

        if self._method == SamplingMethod.random:
            if self._ratio_or_size > 1.0:
                # Absolute count
                keep = self._kept < int(self._ratio_or_size)
            else:
                # Ratio — 1.0 means keep all
                keep = random.random() < self._ratio_or_size
            if keep:
                self._kept += 1
            return keep

        elif self._method == SamplingMethod.systematic:
            if self._ratio_or_size > 1.0:
                # Absolute count: keep every N-th until we have enough
                step = max(1, math.ceil(1.0 / (self._ratio_or_size / max(self._seen, 1))))
                keep = (self._seen - 1) % step == 0 and self._kept < int(self._ratio_or_size)
            else:
                # Every Nth record based on ratio — 1.0 means keep all
                if self._ratio_or_size >= 1.0:
                    keep = True
                else:
                    step = max(1, round(1.0 / self._ratio_or_size))
                    keep = (self._seen % step) == 1
            if keep:
                self._kept += 1
            return keep

        elif self._method == SamplingMethod.stratified:
            key = str(record.get(self._target_column, "_unknown")) if self._target_column else "_all"
            self._strat_counts[key] += 1
            if self._ratio_or_size > 1.0:
                quota = int(self._ratio_or_size)
                keep = self._strat_kept[key] < quota
            else:
                keep = random.random() < self._ratio_or_size
            if keep:
                self._strat_kept[key] += 1
                self._kept += 1
            return keep

        return True
