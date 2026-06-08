"""``ParamSchema`` — a ``DataList`` whose field widths are set by named parameters.

The **Level-1** parameterization stopgap (Phase 3.5).  A concrete schema declares:

- ``_param_defaults`` — the parameter names mapped to their default values;
- ``elements_for(**params)`` — a classmethod returning the ``elements`` dict for those values.

The base supplies the rest: a generic cached :meth:`specialize` (builds a subclass with
``elements = elements_for(**merged)``, cached so **same params → same class object** —
stable schema identity for codegen, like ``IntField.specialize``) and, via
``__init_subclass__``, the default ``elements`` (``elements_for(**_param_defaults)``).  So a
parameterized schema collapses to just ``_param_defaults`` + ``elements_for`` — no
hand-written ``specialize`` / cache / ``type()`` boilerplate.

This is deliberately minimal and **temporary**.  The proper declarative design (Level 2 — a
symbolic ``Param`` unified with ``HwParam``) is deferred; do not build on this.
"""
from __future__ import annotations

from typing import Any, ClassVar

from waveflow.hw.dataschema import DataList


class ParamSchema(DataList):
    """Base for a ``DataList`` whose ``elements`` are a function of named parameters."""

    _param_defaults: ClassVar[dict[str, Any]] = {}
    _specializations: ClassVar[dict[Any, type["ParamSchema"]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # A *concrete* parameterized schema declares its own ``_param_defaults``; give it a
        # fresh specialization cache and its default ``elements``.  Subclasses built BY
        # ``specialize`` inherit the params and already carry their own ``elements`` (set in
        # the ``type()`` call), so they are skipped here.
        if "_param_defaults" in cls.__dict__:
            cls._specializations = {}
            cls.elements = cls.elements_for(**cls._param_defaults)

    @classmethod
    def elements_for(cls, **params: Any) -> dict[str, Any]:
        """Return the ``elements`` dict for the given parameter values (override this)."""
        raise NotImplementedError(f"{cls.__name__} must define elements_for(**params).")

    @classmethod
    def specialize(cls, **params: Any) -> type["ParamSchema"]:
        """Return a cached subclass whose ``elements`` are built for ``params`` (defaults
        filled in).  Keyword-only; same params → the same class object."""
        unknown = set(params) - set(cls._param_defaults)
        if unknown:
            raise TypeError(
                f"{cls.__name__}.specialize() got unknown param(s) {sorted(unknown)}; "
                f"valid params: {sorted(cls._param_defaults)}.")
        merged = {**cls._param_defaults, **params}
        key = (cls, tuple(sorted(merged.items())))
        cached = cls._specializations.get(key)
        if cached is not None:
            return cached
        suffix = "_".join(f"{k}{merged[k]}" for k in sorted(merged))
        specialized = type(f"{cls.__name__}_{suffix}", (cls,), {
            "elements": cls.elements_for(**merged),
            "__module__": cls.__module__,
            "__doc__": cls.__doc__,
        })
        cls._specializations[key] = specialized
        return specialized
