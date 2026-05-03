"""
build.py — build configuration, pipeline primitives, and artifact generation.

Classes
-------
BuildConfig
    Dataclass holding paths and tool settings for a build.

BuildResult
    Dataclass returned by every ``BuildStep.run()`` call.

BuildStep  (ABC)
    Abstract base for any node in a build DAG.  Has a name, optional
    dependency list, a ``resolve_deps()`` hook, and a ``run(config)`` method.

Buildable  (ABC, extends BuildStep)
    A ``BuildStep`` that additionally declares named file outputs and can
    generate their contents as strings.  The default ``run()`` calls
    ``generate()`` for every declared output and writes the results to disk.

BuildDag
    A directed acyclic graph of ``BuildStep`` nodes.  Steps are added via
    ``add()``; ``run()`` executes them in dependency order.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# BuildConfig
# ---------------------------------------------------------------------------

@dataclass
class BuildConfig:
    """
    Configuration for a PySilicon build.

    Parameters
    ----------
    root_dir : Path | str | None
        Project root directory.  All relative output paths are resolved
        against this.  Defaults to the current working directory.
    vitis_version : str | None
        Vitis HLS version in ``"YYYY.M"`` format (e.g. ``"2025.1"``).
        Controls which compatibility files are emitted.  ``None`` means
        conservative / legacy behaviour.
    """

    root_dir: Path | str | None = None
    vitis_version: str | None = None

    def __post_init__(self) -> None:
        self.root_dir = Path.cwd() if self.root_dir is None else Path(self.root_dir)

    def vitis_version_tuple(self) -> tuple[int, int] | None:
        """Parse ``vitis_version`` into a ``(major, minor)`` integer tuple.

        Returns ``None`` when ``vitis_version`` is ``None``.

        Raises ``ValueError`` when the format is not ``"YYYY.M"``.
        """
        if self.vitis_version is None:
            return None
        parts = self.vitis_version.split(".")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid vitis_version '{self.vitis_version}'. Expected format 'YYYY.M'."
            )
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            raise ValueError(
                f"Invalid vitis_version '{self.vitis_version}'. Expected format 'YYYY.M'."
            )

    def needs_legacy_streamutils_cpp(self) -> bool:
        """Return ``True`` when ``streamutils.cpp`` must be included.

        Required for Vitis versions strictly older than ``2025.1``.  When no
        version is specified the conservative default is to include it.
        """
        ver = self.vitis_version_tuple()
        return True if ver is None else ver < (2025, 1)


# ---------------------------------------------------------------------------
# BuildResult
# ---------------------------------------------------------------------------

@dataclass
class BuildResult:
    """
    Outcome of a single :class:`BuildStep` execution.

    Parameters
    ----------
    success : bool
        ``True`` if the step completed without errors.
    message : str
        Human-readable status or error description.
    artifacts : dict[str, Path]
        Mapping of output name → absolute path for any files written to disk.
        Empty for pure validation steps.
    """

    success: bool
    message: str = ""
    artifacts: dict[str, Path] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# BuildStep
# ---------------------------------------------------------------------------

class BuildStep(ABC):
    """
    Abstract base for any node in a build DAG.

    Subclasses implement :meth:`run` and optionally override :attr:`name`,
    :attr:`deps`, and :meth:`resolve_deps`.

    A step that returns ``BuildResult(success=False)`` signals failure to any
    orchestrator; downstream steps that declare this step as a dependency
    should not execute.
    """

    def __init__(self) -> None:
        self._deps: list[BuildStep] = []

    @property
    def name(self) -> str:
        """Human-readable step name; defaults to the class name."""
        return type(self).__name__

    @property
    def deps(self) -> list[BuildStep]:
        """Steps that must succeed before this one runs."""
        return self._deps

    def resolve_deps(self, other_steps: list[BuildStep]) -> None:
        """Populate ``self._deps`` by searching *other_steps*.

        Called by :class:`BuildDag` immediately before the step is registered.
        The default implementation is a no-op; subclasses override it to find
        required antecedent steps and append them to ``self._deps``.
        """

    @abstractmethod
    def run(self, config: BuildConfig) -> BuildResult:
        """Execute the step and return a :class:`BuildResult`."""
        ...


# ---------------------------------------------------------------------------
# Buildable
# ---------------------------------------------------------------------------

class Buildable(BuildStep):
    """
    A :class:`BuildStep` that declares named file outputs and generates their
    content.

    Subclasses implement:

    * :attr:`build_outputs` — ``dict[str, Path]`` mapping output name to a
      path **relative to** ``config.root_dir``.
    * :meth:`generate` — return the file content for a given output key as a
      string.

    The default :meth:`run` implementation iterates over all declared outputs,
    calls :meth:`generate` for each, and writes the results to disk.  Override
    :meth:`run` for more complex behaviour (e.g. binary outputs or conditional
    generation).
    """

    @property
    @abstractmethod
    def build_outputs(self) -> dict[str, Path]:
        """Mapping of output name → path relative to ``config.root_dir``."""
        ...

    @abstractmethod
    def generate(self, key: str, config: BuildConfig) -> str:
        """Return the generated file content for *key* as a string."""
        ...

    def run(self, config: BuildConfig) -> BuildResult:
        artifacts: dict[str, Path] = {}
        try:
            for key, rel_path in self.build_outputs.items():
                content = self.generate(key, config)
                out_path = config.root_dir / rel_path
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(content, encoding="utf-8")
                artifacts[key] = out_path
            return BuildResult(success=True, artifacts=artifacts)
        except Exception as exc:
            return BuildResult(success=False, message=str(exc))


# ---------------------------------------------------------------------------
# BuildDag
# ---------------------------------------------------------------------------

class BuildDag:
    """
    A directed acyclic graph of :class:`BuildStep` nodes.

    Steps are added via :meth:`add`.  Each call invokes
    ``step.resolve_deps(existing_steps)`` so that the step can wire itself to
    previously registered antecedents.  Step names must be unique within a DAG.

    :meth:`run` executes the steps in topological order, propagating failures
    to dependent steps.
    """

    def __init__(self) -> None:
        self._steps: list[BuildStep] = []
        self._names: set[str] = set()

    def add(self, step: BuildStep) -> BuildStep:
        """Register *step*, resolve its deps, and return it."""
        if step.name in self._names:
            raise ValueError(
                f"A step named '{step.name}' already exists in this BuildDag."
            )
        step.resolve_deps(self._steps)
        self._steps.append(step)
        self._names.add(step.name)
        return step

    def run(self, config: BuildConfig) -> dict[str, BuildResult]:
        """Run all steps in dependency order.

        Returns a mapping of step name → :class:`BuildResult`.  A step whose
        dependency failed is recorded as ``success=False`` and skipped.
        """
        order = self._topological_sort()
        results: dict[str, BuildResult] = {}
        failed: set[str] = set()
        for step in order:
            if any(d.name in failed for d in step.deps):
                results[step.name] = BuildResult(
                    success=False, message="Skipped: dependency failed"
                )
                failed.add(step.name)
                continue
            result = step.run(config)
            results[step.name] = result
            if not result.success:
                failed.add(step.name)
        return results

    def _topological_sort(self) -> list[BuildStep]:
        step_by_name = {s.name: s for s in self._steps}
        in_degree: dict[str, int] = {s.name: 0 for s in self._steps}
        adj: dict[str, list[str]] = {s.name: [] for s in self._steps}

        for step in self._steps:
            for dep in step.deps:
                if dep.name not in in_degree:
                    raise ValueError(
                        f"Dependency '{dep.name}' of step '{step.name}' "
                        "is not registered in this BuildDag."
                    )
                adj[dep.name].append(step.name)
                in_degree[step.name] += 1

        queue: deque[str] = deque(
            name for name, deg in in_degree.items() if deg == 0
        )
        order: list[BuildStep] = []
        while queue:
            name = queue.popleft()
            order.append(step_by_name[name])
            for dependent in adj[name]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(self._steps):
            raise ValueError("BuildDag contains a cycle.")
        return order

    def info(self) -> list[dict]:
        """Return structured information about every step in the DAG.

        Each entry is a ``dict`` with three keys:

        * ``"step"`` — step name (str)
        * ``"outputs"`` — list of output file paths relative to the build root,
          as POSIX strings (empty for non-:class:`Buildable` steps)
        * ``"deps"`` — list of dependency step names (str)
        """
        result = []
        for step in self._steps:
            outputs = (
                [p.as_posix() for p in step.build_outputs.values()]
                if isinstance(step, Buildable)
                else []
            )
            result.append({
                "step": step.name,
                "outputs": outputs,
                "deps": [d.name for d in step.deps],
            })
        return result

    def describe(self) -> str:
        """Return a markdown table summarising all steps, their outputs, and deps."""
        header = "| Step | Outputs | Deps |"
        sep    = "|---|---|---|"
        lines  = [header, sep]
        for row in self.info():
            step    = row["step"]
            outputs = ", ".join(row["outputs"]) if row["outputs"] else "—"
            deps    = ", ".join(row["deps"])    if row["deps"]    else "—"
            lines.append(f"| {step} | {outputs} | {deps} |")
        return "\n".join(lines)
