from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pytest

from pysilicon.hw.hw_component import (
    ControlMode,
    HwComponent,
    HwParam,
    SynthContext,
)
from pysilicon.hw.synth import synthesizable
from pysilicon.simulation.simulation import Simulation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sentinel_synth_fn(ctx, inputs, outputs):
    return ""


@dataclass
class ParamComp(HwComponent):
    in_bw: HwParam[int] = 32
    out_bw: HwParam[int] = 64
    max_taps: ClassVar[int] = 16


# ---------------------------------------------------------------------------
# @synthesizable decorator
# ---------------------------------------------------------------------------

def test_synthesizable_no_args_sets_flag():
    @synthesizable
    def my_fn(self):
        pass

    assert my_fn._is_synthesizable is True
    assert my_fn._synth_fn is None


def test_synthesizable_with_synth_fn():
    @synthesizable(synth_fn=_sentinel_synth_fn)
    def my_fn(self):
        pass

    assert my_fn._is_synthesizable is True
    assert my_fn._synth_fn is _sentinel_synth_fn


def test_synthesizable_parens_no_fn_sets_flag():
    @synthesizable()
    def my_fn(self):
        pass

    assert my_fn._is_synthesizable is True
    assert my_fn._synth_fn is None


def test_synthesizable_wraps_original_function():
    @synthesizable
    def compute(self, x):
        return x + 1

    assert compute.__name__ == "compute"


# ---------------------------------------------------------------------------
# HwParam detection
# ---------------------------------------------------------------------------

import typing


def test_hwparam_detectable_via_get_origin():
    hint = HwParam[int]
    assert typing.get_origin(hint) is HwParam


def test_hwparam_detectable_for_nested_types():
    hint = HwParam[list[int]]
    assert typing.get_origin(hint) is HwParam


# ---------------------------------------------------------------------------
# SynthContext.from_component
# ---------------------------------------------------------------------------

def test_synth_context_extracts_hwparam_fields():
    sim = Simulation()
    comp = ParamComp(sim=sim)
    ctx = SynthContext.from_component(comp)

    assert 'in_bw' in ctx.params
    assert 'out_bw' in ctx.params
    assert ctx.params['in_bw'] == 'IN_BW'
    assert ctx.params['out_bw'] == 'OUT_BW'


def test_synth_context_excludes_classvar():
    sim = Simulation()
    comp = ParamComp(sim=sim)
    ctx = SynthContext.from_component(comp)

    assert 'max_taps' not in ctx.params


def test_synth_context_excludes_plain_fields():
    sim = Simulation()
    comp = ParamComp(sim=sim)
    ctx = SynthContext.from_component(comp)

    # 'name', 'sim', 'endpoints' are plain inherited fields — not HwParam
    assert 'name' not in ctx.params
    assert 'sim' not in ctx.params
    assert 'endpoints' not in ctx.params


# ---------------------------------------------------------------------------
# SynthContext.cpp_param
# ---------------------------------------------------------------------------

def test_cpp_param_returns_template_name_for_hwparam():
    sim = Simulation()
    comp = ParamComp(sim=sim)
    ctx = SynthContext.from_component(comp)

    assert ctx.cpp_param('in_bw') == 'IN_BW'
    assert ctx.cpp_param('out_bw') == 'OUT_BW'


def test_cpp_param_returns_repr_for_classvar():
    sim = Simulation()
    comp = ParamComp(sim=sim)
    ctx = SynthContext.from_component(comp)

    assert ctx.cpp_param('max_taps') == '16'


def test_cpp_param_custom_value():
    sim = Simulation()
    comp = ParamComp(sim=sim, in_bw=128)
    ctx = SynthContext.from_component(comp)

    # HwParam field → template name regardless of runtime value
    assert ctx.cpp_param('in_bw') == 'IN_BW'


# ---------------------------------------------------------------------------
# HwComponent instantiation
# ---------------------------------------------------------------------------

def test_hwcomponent_is_a_component():
    from pysilicon.hw.component import Component
    sim = Simulation()
    comp = HwComponent(sim=sim)
    assert isinstance(comp, Component)


def test_hwcomponent_default_control_mode():
    assert HwComponent.control_mode == ControlMode.AUTO


def test_hwcomponent_control_mode_override():
    class FreeRunComp(HwComponent):
        control_mode: ClassVar[ControlMode] = ControlMode.FREE_RUNNING

    assert FreeRunComp.control_mode == ControlMode.FREE_RUNNING


def test_hwcomponent_subclass_with_hwparam_instantiates():
    sim = Simulation()
    comp = ParamComp(sim=sim, in_bw=16, out_bw=32)
    assert comp.in_bw == 16
    assert comp.out_bw == 32
