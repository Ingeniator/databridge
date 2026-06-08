"""T017 — unit tests for SamplingBuffer."""
import pytest
from databridge.export.sampling import SamplingBuffer
from databridge.export.models import SamplingConfig, SamplingMethod


def _config(method: SamplingMethod, ratio: float, target: str | None = None) -> SamplingConfig:
    return SamplingConfig(method=method, ratio_or_size=ratio, target_column=target)


class TestRandomSampling:
    def test_ratio_1_returns_all(self):
        buf = SamplingBuffer(_config(SamplingMethod.random, 1.0))
        records = [{"id": i} for i in range(100)]
        kept = [r for r in records if buf.feed(r)]
        assert len(kept) == 100

    def test_ratio_preserves_approximate_count(self):
        import random as rng
        rng.seed(42)
        buf = SamplingBuffer(_config(SamplingMethod.random, 0.5))
        records = [{"id": i} for i in range(1000)]
        kept = [r for r in records if buf.feed(r)]
        # Should be roughly 500 ± 10%
        assert 400 <= len(kept) <= 600

    def test_empty_input(self):
        buf = SamplingBuffer(_config(SamplingMethod.random, 0.5))
        assert [r for r in [] if buf.feed(r)] == []

    def test_absolute_count(self):
        buf = SamplingBuffer(_config(SamplingMethod.random, 10.0))
        records = [{"id": i} for i in range(100)]
        kept = [r for r in records if buf.feed(r)]
        assert len(kept) == 10


class TestSystematicSampling:
    def test_every_nth_record(self):
        buf = SamplingBuffer(_config(SamplingMethod.systematic, 0.5))
        records = [{"id": i} for i in range(100)]
        kept = [r for r in records if buf.feed(r)]
        # Systematic at 0.5 keeps roughly every 2nd record
        assert 40 <= len(kept) <= 60

    def test_ratio_1_returns_all(self):
        buf = SamplingBuffer(_config(SamplingMethod.systematic, 1.0))
        records = [{"id": i} for i in range(50)]
        kept = [r for r in records if buf.feed(r)]
        assert len(kept) == 50


class TestStratifiedSampling:
    def test_maintains_subgroup_proportions(self):
        import random as rng
        rng.seed(0)
        buf = SamplingBuffer(_config(SamplingMethod.stratified, 0.5, target="region"))
        records = (
            [{"id": i, "region": "us"} for i in range(100)]
            + [{"id": i + 100, "region": "eu"} for i in range(100)]
        )
        rng.shuffle(records)
        kept = [r for r in records if buf.feed(r)]
        us_kept = [r for r in kept if r["region"] == "us"]
        eu_kept = [r for r in kept if r["region"] == "eu"]
        assert 30 <= len(us_kept) <= 70
        assert 30 <= len(eu_kept) <= 70

    def test_ratio_greater_than_1_is_absolute_quota(self):
        buf = SamplingBuffer(_config(SamplingMethod.stratified, 5.0, target="cat"))
        records = [{"id": i, "cat": "A"} for i in range(20)]
        kept = [r for r in records if buf.feed(r)]
        assert len(kept) == 5

    def test_empty_input(self):
        buf = SamplingBuffer(_config(SamplingMethod.stratified, 0.5, target="x"))
        assert [r for r in [] if buf.feed(r)] == []
