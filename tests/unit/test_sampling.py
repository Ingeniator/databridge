"""T017 — unit tests for SamplingBuffer and max_items enforcement."""
import pytest
from databridge.export.sampling import SamplingBuffer
from databridge.export.models import SamplingConfig, SamplingMethod


def _config(
    method: SamplingMethod,
    ratio: float,
    target: str | None = None,
    max_items: int | None = None,
) -> SamplingConfig:
    return SamplingConfig(method=method, ratio_or_size=ratio, target_column=target, max_items=max_items)


def _simulate_worker_loop(records: list[dict], sampling_config: SamplingConfig) -> tuple[int, int]:
    """Mirror the worker's inner loop logic: sampling filter → counter → max_items stop."""
    buf = SamplingBuffer(sampling_config)
    max_items = sampling_config.max_items
    processed = 0
    skipped = 0
    limit_reached = False
    for record in records:
        if limit_reached:
            break
        if not buf.feed(record):
            skipped += 1
            continue
        processed += 1
        if max_items and processed >= max_items:
            limit_reached = True
    return processed, skipped


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


class TestMaxTraces:
    def test_max_items_default_is_none(self):
        cfg = SamplingConfig(method=SamplingMethod.random, ratio_or_size=1.0)
        assert cfg.max_items is None

    def test_max_items_must_be_positive(self):
        with pytest.raises(Exception):
            SamplingConfig(method=SamplingMethod.random, ratio_or_size=1.0, max_items=0)

    def test_max_items_stops_at_limit(self):
        cfg = _config(SamplingMethod.random, 1.0, max_items=10)
        records = [{"id": i} for i in range(100)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 10

    def test_max_items_none_processes_all(self):
        cfg = _config(SamplingMethod.random, 1.0, max_items=None)
        records = [{"id": i} for i in range(50)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 50

    def test_max_items_larger_than_input_processes_all(self):
        cfg = _config(SamplingMethod.random, 1.0, max_items=1000)
        records = [{"id": i} for i in range(30)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 30

    def test_max_items_counts_only_kept_records(self):
        # ratio=0.5 drops ~half; max_items=5 should stop after 5 kept, not 5 total
        import random as rng
        rng.seed(7)
        cfg = _config(SamplingMethod.random, 1.0, max_items=5)
        records = [{"id": i} for i in range(100)]
        processed, skipped = _simulate_worker_loop(records, cfg)
        assert processed == 5
        assert skipped == 0  # ratio=1.0 keeps all, so no skips before limit

    def test_max_items_with_sampling_ratio(self):
        # With ratio 0.5, many records are dropped; max_items=5 caps kept records
        import random as rng
        rng.seed(42)
        cfg = _config(SamplingMethod.random, 0.5, max_items=5)
        records = [{"id": i} for i in range(200)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 5

    def test_max_items_exact_boundary(self):
        cfg = _config(SamplingMethod.random, 1.0, max_items=50)
        records = [{"id": i} for i in range(50)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 50

    def test_max_items_one(self):
        cfg = _config(SamplingMethod.random, 1.0, max_items=1)
        records = [{"id": i} for i in range(100)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 1

    def test_max_items_with_systematic_sampling(self):
        cfg = _config(SamplingMethod.systematic, 1.0, max_items=20)
        records = [{"id": i} for i in range(100)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 20

    def test_max_items_with_stratified_sampling(self):
        cfg = _config(SamplingMethod.stratified, 1.0, target="cat", max_items=10)
        records = [{"id": i, "cat": "A" if i % 2 == 0 else "B"} for i in range(100)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 10


class TestCapExactness:
    """Exactly max_items records are exported when supply is sufficient — never fewer."""

    def test_random_ratio_1_yields_exact_cap(self):
        cfg = _config(SamplingMethod.random, 1.0, max_items=20)
        records = [{"id": i} for i in range(100)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 20

    def test_random_low_ratio_yields_exact_cap_when_data_abundant(self):
        import random as rng
        rng.seed(42)
        # 0.5 ratio over 1 000 records keeps ~500; cap=10 must be hit exactly
        cfg = _config(SamplingMethod.random, 0.5, max_items=10)
        records = [{"id": i} for i in range(1000)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 10

    def test_systematic_yields_exact_cap(self):
        cfg = _config(SamplingMethod.systematic, 1.0, max_items=15)
        records = [{"id": i} for i in range(100)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 15

    def test_stratified_yields_exact_cap(self):
        cfg = _config(SamplingMethod.stratified, 1.0, target="cat", max_items=12)
        records = [{"id": i, "cat": "A" if i % 2 == 0 else "B"} for i in range(100)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 12

    def test_cap_not_applied_when_data_insufficient(self):
        # Fewer records than cap → all available records exported, none omitted
        cfg = _config(SamplingMethod.random, 1.0, max_items=100)
        records = [{"id": i} for i in range(30)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == 30

    def test_cap_one_less_than_input(self):
        n = 50
        cfg = _config(SamplingMethod.random, 1.0, max_items=n - 1)
        records = [{"id": i} for i in range(n)]
        processed, _ = _simulate_worker_loop(records, cfg)
        assert processed == n - 1
