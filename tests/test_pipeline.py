"""Cache-invalidation contract for src/pipeline.py (eng review D5).

Fake stages, wired a -> b -> c with d independent, stand in for the real ones
so these tests never touch data/raw.
"""

import os
from types import SimpleNamespace

import pytest

from src import definitions, pipeline
from src.pipeline import PipelineError, Stage


def make_stages(calls, fail=()):
    def runner(name):
        def run(ctx):
            calls.append(name)
            if name in fail:
                raise RuntimeError(f"{name} failed")
            (ctx.cache_dir / f"{name}.parquet").write_text(name)
            return [f"{name}.parquet"]

        return run

    return [
        Stage("a", runner("a"), constants=("WINDOW",), raw_inputs=("raw.csv",)),
        Stage("b", runner("b"), constants=("THRESHOLD",), upstream=("a",)),
        Stage("c", runner("c"), upstream=("b",)),
        Stage("d", runner("d"), constants=("SEED",)),
    ]


@pytest.fixture
def env(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "raw.csv").write_text("x\n1\n")
    defs = SimpleNamespace(WINDOW=30, THRESHOLD=0.30, SEED=1)
    calls = []

    def run(target="all", fail=(), targets=pipeline.TARGETS):
        calls.clear()
        pipeline.run_target(
            target,
            stages=make_stages(calls, fail),
            defs=defs,
            raw_dir=raw_dir,
            cache_dir=tmp_path / "cache",
            targets=targets,
            log=lambda msg: None,
        )
        return list(calls)

    return SimpleNamespace(run=run, defs=defs, raw_dir=raw_dir, cache_dir=tmp_path / "cache")


def test_unchanged_inputs_reuse_every_cache(env):
    assert env.run() == ["a", "b", "c", "d"]
    assert env.run() == []


def test_constant_change_rebuilds_that_stage_and_everything_downstream(env):
    env.run()
    env.defs.THRESHOLD = 0.25
    assert env.run() == ["b", "c"]


def test_raw_file_change_rebuilds_its_stage_and_downstream(env):
    env.run()
    (env.raw_dir / "raw.csv").write_text("x\n1\n2\n")
    assert env.run() == ["a", "b", "c"]


def test_missing_output_rebuilds_stage_and_downstream(env):
    env.run()
    (env.cache_dir / "b.parquet").unlink()
    assert env.run() == ["b", "c"]


def test_stage_that_crashes_stays_stale_until_it_succeeds(env):
    with pytest.raises(RuntimeError):
        env.run(fail=("b",))
    assert not (env.cache_dir / "b.meta.json").exists()
    assert env.run() == ["b", "c", "d"]


def test_target_stops_when_upstream_from_another_target_is_stale(env):
    targets = {"first": ("a",), "second": ("b", "c")}
    with pytest.raises(PipelineError, match="python -m src.pipeline first"):
        env.run("second", targets=targets)
    assert env.run("first", targets=targets) == ["a"]
    assert env.run("second", targets=targets) == ["b", "c"]


def test_real_stages_declare_only_defined_constants_and_belong_to_one_target():
    stages = pipeline.default_stages()
    pipeline._check_order(stages)
    for stage in stages:
        for name in stage.constants:
            assert hasattr(definitions, name), f"{stage.name} declares undefined {name}"
    owners = [t for names in pipeline.TARGETS.values() for t in names]
    assert sorted(owners) == sorted(s.name for s in stages)
