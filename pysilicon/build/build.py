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
    dependency list, and a ``run(config) -> BuildResult`` method.

Buildable  (ABC, extends BuildStep)
    A ``BuildStep`` that additionally declares named file outputs and can
    generate their contents as strings.  The default ``run()`` calls
    ``generate()`` for every declared output and writes the results to disk.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
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
    util_dir : Path | str | None
        Sub-directory (relative to ``root_dir``) where shared utility files
        (stream utilities, memory manager headers) are placed.
        Defaults to ``"."`` (same as ``root_dir``).
    vitis_version : str | None
        Vitis HLS version in ``"YYYY.M"`` format (e.g. ``"2025.1"``).
        Controls which compatibility files are emitted.  ``None`` means
        conservative / legacy behaviour.
    copy_memmgr : bool
        When ``True`` (default), ``memmgr.hpp`` and ``memmgr_tb.hpp`` are
        copied into ``util_dir`` alongside the streamutils helpers.
    """

    root_dir: Path | str | None = None
    util_dir: Path | str | None = None
    vitis_version: str | None = None
    copy_memmgr: bool = True

    def __post_init__(self) -> None:
        self.root_dir = Path.cwd() if self.root_dir is None else Path(self.root_dir)
        self.util_dir = Path(".") if self.util_dir is None else Path(self.util_dir)

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

    Subclasses implement :meth:`run` and optionally override :attr:`name`
    and :attr:`deps`.

    A step that returns ``BuildResult(success=False)`` signals failure to any
    orchestrator; downstream steps that declare this step as a dependency
    should not execute.
    """

    @property
    def name(self) -> str:
        """Human-readable step name; defaults to the class name."""
        return type(self).__name__

    @property
    def deps(self) -> list[BuildStep]:
        """Steps that must succeed before this one runs."""
        return []

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

