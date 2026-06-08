"""Tests for the Level-2 parameterization framework (``waveflow.hw.param`` + ``ParamSchema``).

Mirrors the two read-only spikes plus the concrete-behavior regression:

- **leaf** — a schema whose fields are built from its ``Param``\\ s (incl. ``2 * data_bw``
  arithmetic), default resolution, caching, concrete fallback, and instantiate / round-trip;
- **cascade** — nested parameterized schemas sharing params both propagate, with shared
  schema identity and a nested round-trip;
- **concrete-behavior regression** — the defer-if-symbolic decorator is byte-transparent:
  every core ``specialize`` returns the same cached class for concrete calls (the wider
  guarantee — full ``-m "not vitis"`` suite == the 15-failure baseline — is checked at the
  suite level).
"""
from enum import IntEnum

import pytest

from waveflow.hw import (
    DataArray,
    EnumField,
    FloatField,
    IntField,
    MemAddr,
    Param,
    ParamSchema,
    pmax,
)
from waveflow.hw.param import Expr, LazyField, defer_if_symbolic, is_symbolic, resolve_elements


class Mode(IntEnum):
    OFF = 0
    ON = 1


# --- Param / Expr / LazyField primitives --------------------------------------
def test_param_resolves_default_and_env():
    p = Param(32)
    p.name = "awidth"
    assert p.resolve({}) == 32                       # default when absent
    assert p.resolve({"awidth": 64}) == 64           # env override


def test_param_arithmetic_builds_resolvable_expr():
    w = Param(10)
    w.name = "w"
    assert isinstance(2 * w, Expr)
    assert (2 * w).resolve({"w": 7}) == 14           # __rmul__
    assert (w + 1).resolve({"w": 7}) == 8
    assert (w - 3).resolve({"w": 7}) == 4
    assert (2 * w + 1).resolve({"w": 7}) == 15       # composed
    assert (w // 2).resolve({"w": 7}) == 3
    assert pmax(w, 12).resolve({"w": 7}) == 12       # symbolic max


def test_is_symbolic_recurses_into_tuples():
    p = Param(4)
    assert is_symbolic(p) and is_symbolic((1, p, 2)) and is_symbolic([p])
    assert not is_symbolic(8) and not is_symbolic((1, 2, 3)) and not is_symbolic("ap_uint")


def test_lazyfield_resolves_args_and_kwargs():
    w = Param(8)
    w.name = "w"
    lf = IntField.specialize(w, signed=False)
    assert isinstance(lf, LazyField)
    cls = lf.resolve({"w": 12})
    assert cls is IntField.specialize(12, signed=False)   # resolves to the cached concrete class


# --- defer-if-symbolic is byte-transparent for concrete calls -----------------
def test_concrete_specialize_is_byte_identical():
    # the load-bearing invariant: with no Param present, each core specialize is unchanged
    assert IntField.specialize(16) is IntField.specialize(16)
    assert IntField.specialize(8, signed=False) is IntField.specialize(8, signed=False)
    assert FloatField.specialize(32) is FloatField.specialize(32)
    assert MemAddr.specialize(64) is MemAddr.specialize(64)
    assert EnumField.specialize(Mode) is EnumField.specialize(Mode)
    F32 = FloatField.specialize(32)
    assert DataArray.specialize(F32, max_shape=(4,)) is DataArray.specialize(F32, max_shape=(4,))
    assert IntField.specialize(16).cpp_type == "ap_int<16>"   # value unchanged


@pytest.mark.parametrize("call", [
    lambda p: IntField.specialize(p),
    lambda p: IntField.specialize(p, signed=False),
    lambda p: FloatField.specialize(p),
    lambda p: MemAddr.specialize(p),
    lambda p: EnumField.specialize(Mode, bitwidth=p),
    lambda p: DataArray.specialize(IntField.specialize(8), max_shape=(p,)),
])
def test_core_specialize_defers_on_param(call):
    p = Param(8)
    p.name = "p"
    assert isinstance(call(p), LazyField)


def test_defer_decorator_standalone():
    @defer_if_symbolic
    def f(x, y=0):
        return ("concrete", x, y)
    assert f(1, y=2) == ("concrete", 1, 2)           # concrete: passthrough
    p = Param(3)
    p.name = "p"
    lf = f(p, y=2)
    assert isinstance(lf, LazyField) and lf.resolve({"p": 9}) == ("concrete", 9, 2)


# --- leaf schema --------------------------------------------------------------
class Region(ParamSchema):
    awidth = Param(32)
    elements = {
        "addr": IntField.specialize(awidth, signed=False),
        "row_stride": IntField.specialize(awidth, signed=True),
        "wide": IntField.specialize(2 * awidth, signed=True),   # arithmetic-derived width
    }


def test_leaf_defaults_and_param_naming():
    assert set(Region._params) == {"awidth"}
    assert Region._params["awidth"].name == "awidth"
    assert Region.get_element_schema("addr").get_bitwidth() == 32
    assert Region.get_element_schema("addr").cpp_type == "ap_uint<32>"
    assert Region.get_element_schema("wide").get_bitwidth() == 64        # 2 * 32


def test_leaf_specialize_and_arithmetic():
    R = Region.specialize(awidth=48)
    assert R.get_element_schema("addr").get_bitwidth() == 48
    assert R.get_element_schema("row_stride").cpp_type == "ap_int<48>"
    assert R.get_element_schema("wide").get_bitwidth() == 96            # 2 * 48


def test_leaf_caching_same_params_same_class():
    assert Region.specialize(awidth=48) is Region.specialize(awidth=48)
    assert Region.specialize(awidth=64) is not Region.specialize(awidth=48)
    assert Region.specialize() is Region.specialize()                  # defaults


def test_leaf_unknown_param_guard():
    with pytest.raises(TypeError, match="unknown param"):
        Region.specialize(bogus=10)


def test_leaf_instantiate_and_roundtrip():
    R = Region.specialize(awidth=16)
    inst = R(addr=5, row_stride=-3, wide=1234)
    assert inst.addr == 5 and inst.row_stride == -3
    restored = R().deserialize(inst.serialize(32), 32)
    assert restored.is_close(inst)               # is_close: robust to wide (multi-word) fields


# --- cascade: nested parameterized schemas sharing params ---------------------
class Scalar(ParamSchema):
    awidth = Param(32)
    data_bw = Param(32)
    elements = {
        "addr": IntField.specialize(awidth, signed=False),
        "imm": IntField.specialize(data_bw, signed=True),
    }


class Cmd(ParamSchema):
    mem_awidth = Param(32)
    data_bw = Param(32)
    elements = {
        "n": IntField.specialize(16, signed=False),          # concrete element (passthrough)
        "a": Region.specialize(awidth=mem_awidth),           # nested, shares mem_awidth
        "alpha": Scalar.specialize(awidth=mem_awidth, data_bw=data_bw),
    }


def test_cascade_elements_are_lazy_until_specialized():
    # the nested specialize calls deferred at class-definition time
    assert isinstance(Cmd._lazy_elements["a"], LazyField)
    assert isinstance(Cmd._lazy_elements["alpha"], LazyField)
    assert Cmd._lazy_elements["n"] is IntField.specialize(16, signed=False)   # concrete stays


def test_cascade_both_params_propagate():
    C = Cmd.specialize(mem_awidth=64, data_bw=24)
    region = C.get_element_schema("a")
    scalar = C.get_element_schema("alpha")
    assert region.get_element_schema("addr").get_bitwidth() == 64
    assert scalar.get_element_schema("addr").get_bitwidth() == 64           # shared mem_awidth
    assert scalar.get_element_schema("imm").get_bitwidth() == 24            # data_bw
    assert C.get_element_schema("n").get_bitwidth() == 16                   # concrete unchanged


def test_cascade_shared_schema_identity():
    C = Cmd.specialize(mem_awidth=64, data_bw=24)
    # the nested Region is the SAME cached class as a direct Region.specialize(64)
    assert C.get_element_schema("a") is Region.specialize(awidth=64)
    assert C.get_element_schema("alpha") is Scalar.specialize(awidth=64, data_bw=24)


def test_cascade_defaults_and_caching():
    assert Cmd.get_element_schema("a") is Region.specialize(awidth=32)      # defaults cascade
    assert Cmd.specialize(mem_awidth=64, data_bw=24) is Cmd.specialize(mem_awidth=64, data_bw=24)


def test_cascade_nested_roundtrip():
    C = Cmd.specialize(mem_awidth=40, data_bw=12)
    inst = C()
    inst.a = {"addr": 7, "row_stride": -2, "wide": 99}
    inst.alpha = {"addr": 3, "imm": -5}
    inst.n = 9
    restored = C().deserialize(inst.serialize(32), 32)
    assert restored.is_close(inst)               # nested round-trip (incl. wide fields)
    assert restored.a.addr == 7 and restored.alpha.imm == -5 and restored.n == 9


# --- DataArray: parameterized element type AND parameterized length -----------
class ArrBuf(ParamSchema):
    w = Param(8)
    n = Param(4)
    elements = {
        "data": DataArray.specialize(IntField.specialize(w, signed=False), max_shape=(n,)),
    }


def test_dataarray_parameterized_element_and_length():
    B = ArrBuf.specialize(w=16, n=6)
    arr = B.get_element_schema("data")
    assert arr.element_type.get_bitwidth() == 16        # parameterized element width
    assert tuple(arr.max_shape) == (6,)                 # parameterized array length
    # defaults
    arr0 = ArrBuf.get_element_schema("data")
    assert arr0.element_type.get_bitwidth() == 8 and tuple(arr0.max_shape) == (4,)


def test_resolve_elements_helper_passes_concrete_through():
    U8 = IntField.specialize(8, signed=False)
    out = resolve_elements({"x": U8, "meta": {"schema": U8, "description": "d"}}, {})
    assert out["x"] is U8 and out["meta"]["schema"] is U8 and out["meta"]["description"] == "d"
