r"""Pipeline runner with hash-stamped stage caches (eng review D5).

Usage:
    python -m src.pipeline ingest|model|bootstrap|all
    python -m src.pipeline status

Each stage writes data/cache/<stage>.meta.json after it succeeds. The meta
holds a fingerprint of everything the stage's result depends on:

    - the values of the src/definitions.py constants the stage declares,
    - the size and mtime of the raw files it reads,
    - the fingerprints of its upstream stages.

A stage is stale when its fingerprint differs from the saved one, an output it
recorded is missing, or any upstream stage is stale. Stale stages rebuild, and
so does everything downstream of them.

Targets group the stages so the expensive bootstrap never runs implicitly:

    ingest -> trials -> logs -> completeness       (target: ingest)
    features -> model ----------> charts -> page   (target: model)
                   \-> bootstrap -/                (target: bootstrap)

A target runs its stale stages in order. If a stage needs an upstream stage
from another target and that stage is stale, the run stops and names the
target to run first. `all` runs every stage.
"""

import argparse
import hashlib
import importlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterable, Union

from src import definitions


class PipelineError(Exception):
    pass


@dataclass(frozen=True)
class Context:
    raw_dir: Path
    cache_dir: Path


# A stage's run function takes a Context and returns the paths it wrote,
# relative to cache_dir. A string "module:function" is imported only when the
# stage runs.
RunFn = Union[str, Callable[[Context], Iterable[str]]]


@dataclass(frozen=True)
class Stage:
    name: str
    run: RunFn
    constants: tuple[str, ...] = ()
    raw_inputs: tuple[str, ...] = ()
    upstream: tuple[str, ...] = ()


def default_stages(defs: ModuleType = definitions) -> list[Stage]:
    """The study's stages in dependency order (eng review D1 layout)."""
    return [
        Stage(
            "ingest",
            "src.ingest:run",
            raw_inputs=defs.TRANSACTIONS_FILES + (defs.MEMBERS_FILE,),
        ),
        Stage(
            "trials",
            "src.trials:run",
            constants=(
                "TRIAL_PLAN_DAYS",
                "TRIAL_AMOUNT_PAID",
                "TRIAL_LENGTH_DAYS",
                "TRIAL_EXPIRE_MIN_OFFSET_DAYS",
                "TRIAL_EXPIRE_MAX_OFFSET_DAYS",
                "BURN_IN_START",
                "MAX_REGISTRATION_LAG_DAYS",
                "CONVERSION_WINDOW_DAYS",
                "FOLLOWUP_END",
            ),
            upstream=("ingest",),
        ),
        Stage(
            "logs",
            "src.logs:run",
            constants=(
                "BEHAVIOR_WINDOW_DAYS",
                "MIN_VALID_TOTAL_SECS",
                "MAX_VALID_TOTAL_SECS",
                "MEMBER_SAMPLE_SIZE",
                "MEMBER_SAMPLE_SEED",
            ),
            raw_inputs=defs.USER_LOGS_FILES,
            upstream=("ingest", "trials"),
        ),
        Stage(
            "completeness",
            "src.completeness:run",
            constants=("BEHAVIOR_WINDOW_DAYS", "GAP_REFERENCE_WEEKS", "GAP_RELATIVE_DROP"),
            upstream=("trials", "logs"),
        ),
        Stage(
            "features",
            "src.features:run",
            constants=(
                "TOP_CITIES",
                "MIN_VALID_AGE",
                "MAX_VALID_AGE",
                "AGE_BAND_UPPER_BOUNDS",
                "REG_LAG_BAND_LOWER_BOUNDS_DAYS",
            ),
            upstream=("ingest", "trials", "logs", "completeness"),
        ),
        Stage(
            "model",
            "src.model:run",
            constants=(
                "MIN_CAMPAIGN_TRIALS",
                "MATCH_TOP_REGISTERED_VIA",
                "DECOMPOSITION_SEED",
            ),
            upstream=("features",),
        ),
        Stage(
            "bootstrap",
            "src.bootstrap:run",
            constants=("BOOTSTRAP_REPLICATES", "BOOTSTRAP_SEED"),
            upstream=("features", "model"),
        ),
        Stage(
            "charts",
            "src.charts:run",
            constants=("MIN_PUBLISHED_CELL",),
            upstream=("trials", "completeness", "model", "bootstrap"),
        ),
        Stage(
            "page",
            "src.page:run",
            constants=("MIN_PUBLISHED_CELL",),
            upstream=("model", "bootstrap", "charts"),
        ),
    ]


TARGETS = {
    "ingest": ("ingest", "trials", "logs", "completeness"),
    "model": ("features", "model", "charts", "page"),
    "bootstrap": ("bootstrap",),
}


def target_stages(target: str, stages: list[Stage], targets: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    if target == "all":
        return tuple(s.name for s in stages)
    if target not in targets:
        raise PipelineError(f"unknown target {target!r}; choose from {sorted(targets) + ['all']}")
    return targets[target]


def _check_order(stages: list[Stage]) -> None:
    seen: set[str] = set()
    for stage in stages:
        for up in stage.upstream:
            if up not in seen:
                raise PipelineError(f"stage {stage.name!r} lists upstream {up!r}, which is not defined before it")
        if stage.name in seen:
            raise PipelineError(f"stage {stage.name!r} is defined twice")
        seen.add(stage.name)


def fingerprint(stage: Stage, defs: ModuleType, raw_dir: Path, upstream_fps: dict[str, str]) -> str:
    constants = {}
    for name in stage.constants:
        if not hasattr(defs, name):
            raise PipelineError(f"stage {stage.name!r} declares constant {name!r}, which src/definitions.py does not define")
        constants[name] = repr(getattr(defs, name))

    raw = {}
    for file_name in stage.raw_inputs:
        path = raw_dir / file_name
        try:
            st = path.stat()
        except FileNotFoundError:
            raise PipelineError(f"stage {stage.name!r} needs raw file {path}, which does not exist") from None
        raw[file_name] = [st.st_size, st.st_mtime_ns]

    payload = {
        "stage": stage.name,
        "constants": constants,
        "raw": raw,
        "upstream": {up: upstream_fps[up] for up in stage.upstream},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _meta_path(cache_dir: Path, stage: Stage) -> Path:
    return cache_dir / f"{stage.name}.meta.json"


def _read_meta(cache_dir: Path, stage: Stage) -> dict | None:
    path = _meta_path(cache_dir, stage)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def stage_status(
    stages: list[Stage], defs: ModuleType, raw_dir: Path, cache_dir: Path
) -> dict[str, tuple[bool, str, str]]:
    """Map stage name -> (stale, fingerprint, reason), in dependency order."""
    _check_order(stages)
    status: dict[str, tuple[bool, str, str]] = {}
    fps: dict[str, str] = {}
    for stage in stages:
        fp = fingerprint(stage, defs, raw_dir, fps)
        fps[stage.name] = fp
        meta = _read_meta(cache_dir, stage)
        stale_ups = [up for up in stage.upstream if status[up][0]]
        if meta is None:
            reason = "never built"
        elif meta.get("fingerprint") != fp:
            reason = "inputs or rules changed"
        elif missing := [o for o in meta.get("outputs", []) if not (cache_dir / o).exists()]:
            reason = f"output missing: {missing[0]}"
        elif stale_ups:
            reason = f"upstream stale: {', '.join(stale_ups)}"
        else:
            reason = "fresh"
        status[stage.name] = (reason != "fresh", fp, reason)
    return status


def _resolve(run: RunFn) -> Callable[[Context], Iterable[str]]:
    if callable(run):
        return run
    module_name, _, func_name = run.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise PipelineError(f"{module_name} is not written yet") from None
        raise
    return getattr(module, func_name)


def run_target(
    target: str,
    stages: list[Stage] | None = None,
    defs: ModuleType = definitions,
    raw_dir: Path | None = None,
    cache_dir: Path | None = None,
    targets: dict[str, tuple[str, ...]] = TARGETS,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Rebuild the stale stages of `target`. Returns the names of stages run."""
    stages = default_stages(defs) if stages is None else stages
    raw_dir = defs.RAW_DIR if raw_dir is None else raw_dir
    cache_dir = defs.CACHE_DIR if cache_dir is None else cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)

    wanted = target_stages(target, stages, targets)
    status = stage_status(stages, defs, raw_dir, cache_dir)
    by_name = {s.name: s for s in stages}
    ran: list[str] = []

    for stage in stages:
        if stage.name not in wanted:
            continue
        stale, fp, reason = status[stage.name]
        if not stale:
            log(f"[{stage.name}] fresh, reusing cache")
            continue
        blocked = [up for up in stage.upstream if status[up][0]]
        if blocked:
            owner = next((t for t, names in targets.items() if blocked[0] in names), "all")
            raise PipelineError(
                f"stage {stage.name!r} needs {blocked[0]!r}, which is out of date "
                f"({status[blocked[0]][2]}). Run `python -m src.pipeline {owner}` first."
            )

        log(f"[{stage.name}] rebuilding ({reason})")
        meta_path = _meta_path(cache_dir, by_name[stage.name])
        # Remove the old stamp first, so a crash mid-stage leaves the stage stale.
        meta_path.unlink(missing_ok=True)
        outputs = sorted(_resolve(stage.run)(Context(raw_dir=raw_dir, cache_dir=cache_dir)) or [])
        missing = [o for o in outputs if not (cache_dir / o).exists()]
        if missing:
            raise PipelineError(f"stage {stage.name!r} reported output {missing[0]!r} but did not write it")

        meta = {
            "stage": stage.name,
            "fingerprint": fp,
            "outputs": outputs,
            "constants": {name: repr(getattr(defs, name)) for name in stage.constants},
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        tmp = meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        os.replace(tmp, meta_path)
        ran.append(stage.name)
        status[stage.name] = (False, fp, "fresh")

    return ran


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.pipeline", description=__doc__.split("\n\n")[0])
    parser.add_argument("target", choices=[*TARGETS, "all", "status"])
    args = parser.parse_args(argv)
    try:
        if args.target == "status":
            stages = default_stages()
            for name, (stale, _, reason) in stage_status(
                stages, definitions, definitions.RAW_DIR, definitions.CACHE_DIR
            ).items():
                print(f"{name:<13} {'STALE' if stale else 'fresh':<6} {reason}")
            return 0
        run_target(args.target)
        return 0
    except PipelineError as exc:
        print(f"pipeline: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
