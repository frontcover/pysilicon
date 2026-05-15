# BuildDag Extension Plan

## Motivation

The current `BuildDag` works well for pure code-generation pipelines where every step
writes files.  The poly accelerator reveals a broader need: pipelines where build steps,
validation steps, and simulation steps are interleaved and share in-memory results as
well as files.  The goal is one unified DAG that covers the full flow:

```
python_sim → validate_timing → [timing_diagram]
           ↘
             gen_cpp → write_inputs → csim → validate_csim → csynth → inspect_synth → rtl_sim → inspect_rtl
```

---

## Key Gap in the Current Design

`BuildStep.run(config)` receives only a `BuildConfig`.  Steps cannot read the outputs
of their dependencies at runtime.  The result is that complex flows must be orchestrated
outside the DAG (as `PolyTest` currently does imperatively).

The fix is one sentence: **change `run(config)` to `run(config, results)`** where
`results` is the accumulated `dict[str, BuildResult]` of all completed prior steps.

---

## Abstractions

### `BuildArtifact`

A single named output from a step — either a file on disk or an in-memory Python
object.  Two concrete subclasses; polymorphism eliminates type-dispatch branching.

```python
class BuildArtifact(ABC):
    """Base for any value produced by a BuildStep."""
    timestamp: float   # time.time() when this artifact was created

    @abstractmethod
    def is_fresh_relative_to(self, other: BuildArtifact) -> bool:
        """True if this artifact is newer than *other*."""
        ...

@dataclass
class FileArtifact(BuildArtifact):
    """A file written to disk.  Freshness is checked via the file's mtime."""
    path: Path
    timestamp: float = field(default_factory=time.time)

    def is_fresh_relative_to(self, other: BuildArtifact) -> bool:
        return self.path.stat().st_mtime > other.timestamp

@dataclass
class ObjectArtifact(BuildArtifact):
    """An in-memory Python object (PolySimResult, DataFrame, parsed report, …)."""
    value: Any
    timestamp: float = field(default_factory=time.time)

    def is_fresh_relative_to(self, other: BuildArtifact) -> bool:
        return self.timestamp > other.timestamp
```

**Why not subclass `BuildResult` instead (e.g. `FileBuildResult`, `ObjectBuildResult`)?**
`BuildResult` describes the *run* (success, message, when).  `BuildArtifact` describes
a *value* that downstream steps consume.  A single step commonly produces both — e.g.
`CSimStep` writes output files **and** returns a parsed result object.  Subclassing
`BuildResult` per artifact type would force one type per step and collapse two distinct
concerns into one class.  Typed artifact subclasses compose; typed result subclasses do
not.

**Source files as `FileArtifact`s**
Leaf steps (no step dependencies) compare against source files.  Wrapping them as
`FileArtifact` with `path.stat().st_mtime` as the timestamp lets the DAG use the same
freshness check for both inter-step and source-to-step edges:

```python
def source_artifact(path: Path) -> FileArtifact:
    return FileArtifact(path=path, timestamp=path.stat().st_mtime)
```

---

### `BuildResult`

Outcome of a single step execution.  Extended from current version.

```python
@dataclass
class BuildResult:
    success: bool
    message: str = ""
    artifacts: dict[str, BuildArtifact] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def object(self, name: str) -> Any:
        """Return the Python object value of an ObjectArtifact."""
        return self.artifacts[name].value           # type: ignore[attr-defined]

    def path(self, name: str) -> Path:
        """Return the Path of a FileArtifact."""
        return self.artifacts[name].path            # type: ignore[attr-defined]
```

Downstream steps read typed artifacts:
- `results['python_sim'].object('sim_result')` → `PolySimResult`
- `results['gen_cpp'].path('include')` → `Path`
- `results['csim'].path('data_dir')` → `Path`
- `results['csim'].object('parsed')` → DataFrame

---

### `BuildStep`

Extended from current version.  Two changes:

1. `run(config, results)` — receives the accumulated results dict.
2. `is_fresh(config, results) -> bool` — optional skip predicate.

```python
class BuildStep(ABC):
    optional: bool = False   # class-level default; set True for opt-in steps

    @property
    def name(self) -> str: ...

    @property
    def deps(self) -> list[BuildStep]: ...

    def resolve_deps(self, other_steps: list[BuildStep]) -> None:
        """Wire self._deps by searching other_steps (called by BuildDag.add)."""

    def is_fresh(self, config: BuildConfig, results: dict[str, BuildResult]) -> bool:
        """Return True to skip this step.

        Default: a step is fresh when ALL of its declared file outputs exist AND
        are newer than ALL file artifacts produced by its dependencies.
        Non-file (object) artifacts are never considered fresh — they are always
        recomputed.  Override for custom logic (e.g. hash-based caching).
        """
        return False   # conservative default; subclasses opt in

    @abstractmethod
    def run(self, config: BuildConfig, results: dict[str, BuildResult]) -> BuildResult:
        """Execute the step.  Read dependency outputs via results[dep.name]."""
        ...
```

**`Buildable` stays unchanged** — it is a `BuildStep` specialised for file generation.
Its `run()` override signature changes to `run(config, results)` for consistency, but
it ignores `results` unless the generated content depends on a prior step's output.

---

### `BuildDag`

Extended from current version.  Three changes:

1. `run()` threads the accumulated `results` dict into each step's `run()` call.
2. `run(skip_fresh=True)` — skip steps that report `is_fresh()`.
3. `run(include_optional=[...])` — opt-in list of optional step names to include.

```python
class BuildDag:
    def add(self, step: BuildStep) -> BuildStep: ...   # unchanged

    def run(
        self,
        config: BuildConfig,
        skip_fresh: bool = False,
        include_optional: list[str] | None = None,
    ) -> dict[str, BuildResult]:
        order = self._topological_sort()
        results: dict[str, BuildResult] = {}
        failed: set[str] = set()
        opted_out: set[str] = set()

        for step in order:
            # Skip optional steps unless explicitly included
            if step.optional and (
                include_optional is None or step.name not in include_optional
            ):
                opted_out.add(step.name)
                continue

            # Skip if a dependency failed or was opted out
            if any(d.name in failed or d.name in opted_out for d in step.deps):
                results[step.name] = BuildResult(
                    success=False, message="Skipped: dependency unavailable"
                )
                failed.add(step.name)
                continue

            # Skip if outputs are fresh
            if skip_fresh and step.is_fresh(config, results):
                results[step.name] = BuildResult(
                    success=True, message="Skipped: outputs are fresh", artifacts={}
                )
                continue

            result = step.run(config, results)
            results[step.name] = result
            if not result.success:
                failed.add(step.name)

        return results
```

---

## Poly Pipeline Sketch

How the poly flow looks once these abstractions are in place:

```python
dag = BuildDag()

# --- Simulation ---
py_sim    = dag.add(PySimStep(nsamp=100, in_bw=32, unroll_factor=1))
           # produces: artifacts['sim_result'] (PolySimResult), artifacts['log'] (Path)

val_time  = dag.add(ValidateTimingStep())
           # deps: [py_sim]; reads sim_result + log; raises on failure
           # produces: artifacts['durations'] (dict of ns timings)

t_diagram = dag.add(TimingDiagramStep(), optional=True)
           # deps: [py_sim, val_time]; produces artifacts['plot'] (Path)

# --- Build ---
gen_cpp   = dag.add(GenCppStep())
           # deps: []; produces artifacts['include'] (Path)

write_in  = dag.add(WriteInputsStep())
           # deps: [py_sim]; produces artifacts['data_dir'] (Path)

csim      = dag.add(CSimStep())
           # deps: [gen_cpp, write_in]; invokes Vitis; produces artifacts['data_dir']

val_csim  = dag.add(ValidateCSimStep())
           # deps: [py_sim, csim]; compares outputs; raises on mismatch

csynth    = dag.add(CSynthStep())
           # deps: [gen_cpp]; produces artifacts['report'] (Path)

insp_syn  = dag.add(InspectSynthStep())
           # deps: [csynth]; parses XML; produces artifacts['loop_df']

rtl_sim   = dag.add(RtlSimStep())
           # deps: [csynth]; produces artifacts['vcd'] (Path)

insp_rtl  = dag.add(InspectRtlStep())
           # deps: [rtl_sim]; reads VCD; produces artifacts['timing']

# Run everything up to csynth, skipping fresh file steps
results = dag.run(config, skip_fresh=True)

# Run with timing diagram too
results = dag.run(config, include_optional=['TimingDiagramStep'])
```

---

## Open Questions

1. **Validation failures**: should a step that raises `AssertionError` set
   `success=False` and continue, or propagate as an exception?  Proposal: catch and
   set `success=False`, so the DAG can report all failures rather than stopping at the
   first.

2. **`is_fresh` default**: the conservative default (`return False`) means no skipping
   unless explicitly opted in.  Alternatively, `Buildable` steps could auto-implement
   freshness based on file mtimes.  Lean toward explicit opt-in to avoid surprising
   skips.

3. **Parameterised steps**: the three timing configurations (uf=1/bw=32, uf=2/bw=32,
   uf=2/bw=64) look like three separate `PySimStep` instances in the same DAG, each
   with a different name.  The DAG handles this naturally since step names are unique.

4. **Backward compatibility**: existing `BuildStep.run(config)` callers break when the
   signature becomes `run(config, results)`.  Options: (a) make `results` a keyword
   argument with default `{}`, (b) provide a shim base class.  Keyword-arg default is
   simpler.
