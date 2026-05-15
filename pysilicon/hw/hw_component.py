from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import ClassVar, Generic, TypeVar
import typing

from pysilicon.hw.component import Component

T = TypeVar('T')


class HwParam(Generic[T]):
    """Marks a dataclass field as a C++ template parameter.

    In simulation the field behaves as a normal Python attribute (dataclass
    does not enforce types). At build time the extractor collects all HwParam
    fields from ``get_type_hints()`` and maps them to C++ template names.

    C++ name convention: ``field_name.upper()`` — ``in_bw`` → ``IN_BW``.
    """


class ControlMode(Enum):
    AUTO = auto()           # inferred from HwStmt root at build time
    FREE_RUNNING = auto()   # ap_ctrl_none  (WhileStmt at root)
    PER_INVOCATION = auto() # ap_ctrl_chain (SeqStmt at root)


@dataclass
class SynthContext:
    """Parameter context passed to every ``synth_fn`` during codegen."""

    component: HwComponent
    params: dict[str, str]  # Python name → C++ template param name

    def cpp_param(self, py_name: str) -> str:
        """Return the C++ expression for a parameter.

        Returns the template parameter name (e.g. ``'IN_BW'``) for
        ``HwParam`` fields, or ``repr(value)`` for ``ClassVar`` literals.
        """
        if py_name in self.params:
            return self.params[py_name]
        return repr(getattr(self.component, py_name))

    @classmethod
    def from_component(cls, comp: HwComponent) -> SynthContext:
        import sys
        params: dict[str, str] = {}
        comp_type = type(comp)
        # Walk only the HwComponent subclass layers — stop before HwComponent
        # itself to avoid evaluating SimObj/Component TYPE_CHECKING annotations.
        for klass in comp_type.__mro__:
            if klass is HwComponent:
                break
            if not issubclass(klass, HwComponent):
                break
            raw_ann = vars(klass).get('__annotations__', {})
            mod = sys.modules.get(klass.__module__)
            globs: dict = vars(mod) if mod is not None else {}
            for name, hint_val in raw_ann.items():
                if isinstance(hint_val, str):
                    try:
                        hint = eval(hint_val, globs)  # noqa: S307
                    except Exception:
                        continue
                else:
                    hint = hint_val
                if typing.get_origin(hint) is HwParam:
                    params[name] = name.upper()
        return cls(component=comp, params=params)


class HwComponent(Component):
    """Base class for synthesizable hardware components.

    Subclasses annotate synthesis template parameters with ``HwParam[T]``
    and mark compute methods with ``@synthesizable``.
    """

    control_mode: ClassVar[ControlMode] = ControlMode.AUTO
