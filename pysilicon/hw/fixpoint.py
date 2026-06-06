"""``FixedField`` — a Vitis-bit-exact ``ap_fixed`` DataSchema type.

An ``ap_fixed<W, I>`` is a ``W``-bit integer with an implied binary point ``I``
bits from the MSB (value = ``stored_int * 2**-(W-I)``), so ``FixedField``
subclasses :class:`~pysilicon.hw.dataschema.IntField` and **reuses its W-bit word
serialization verbatim**, overriding only:

* the value<->stored-int conversion — quantization, delegated to
  :mod:`pysilicon.utils.fixputils` so it is bit-exact with Vitis; and
* the emitted C++ type — ``ap_fixed<W,I,Q,O>`` / ``ap_ufixed<W,I,Q,O>``.

``.val`` holds the **quantized real value** (a float, like ``FloatField``);
assignment quantizes per the Q/O modes. Defaults ``AP_TRN`` + ``AP_WRAP`` + signed
match Vitis's ``ap_fixed`` defaults, so a default-constructed field already matches
the default hardware type.

Import is one-way (``fixpoint`` imports ``dataschema``/``fixputils``, never the
reverse — no cycle). Users import ``FixedField`` from here, not from ``dataschema``
(decision 2: explicit module, no lazy re-export needed).
"""
from __future__ import annotations

from typing import Any, ClassVar

from pysilicon.hw.dataschema import IntField
from pysilicon.utils import fixputils
from pysilicon.utils.fixputils import AP_TRN, AP_WRAP


class FixedField(IntField):
    """Fixed-point scalar field, bit-exact with ``ap_fixed<W, I, Q, O>``.

    Class attributes (set via :meth:`specialize`): ``bitwidth`` (= W, total bits),
    ``int_bits`` (= I, integer bits, sign-inclusive when signed), ``signed``,
    ``q_mode`` (quantization), ``o_mode`` (overflow), and the ``ap_fixed`` /
    ``ap_ufixed`` ``cpp_type``.
    """

    bitwidth: ClassVar[int] = 32
    int_bits: ClassVar[int] = 16          # the ap_fixed I (avoids the E741 single-letter name)
    signed: ClassVar[bool] = True
    q_mode: ClassVar[str] = AP_TRN
    o_mode: ClassVar[str] = AP_WRAP
    cpp_type: ClassVar[str] = "ap_fixed<32, 16, AP_TRN, AP_WRAP>"
    can_gen_include: ClassVar[bool] = False
    _specializations: ClassVar[dict] = {}

    @classmethod
    def specialize(  # type: ignore[override]
        cls,
        W: int,
        I: int,  # noqa: E741 — ap_fixed integer-bit count
        signed: bool = True,
        q_mode: str = AP_TRN,
        o_mode: str = AP_WRAP,
        **kwargs: Any,
    ) -> type[FixedField]:
        """Return a cached specialized ``FixedField`` for ``ap_fixed<W, I, q, o>``."""
        if W <= 0:
            raise ValueError("W (total bits) must be positive.")
        fixputils._validate_format(W, q_mode, o_mode)

        overrides = cls.validate_specialize_kwargs(kwargs)
        override_items = tuple(sorted(overrides.items()))
        key = (cls, int(W), int(I), bool(signed), q_mode, o_mode, override_items)
        cached = cls._specializations.get(key)
        if cached is not None:
            return cached

        base = "ap_fixed" if signed else "ap_ufixed"
        cpp_type = f"{base}<{W}, {I}, {q_mode}, {o_mode}>"
        prefix = "Fixed" if signed else "UFixed"
        subclass_name = f"{prefix}{W}_{I}"

        specialized_attrs = cls.merge_specialize_attrs(
            {
                "bitwidth": int(W),
                "int_bits": int(I),
                "signed": bool(signed),
                "q_mode": q_mode,
                "o_mode": o_mode,
                "cpp_type": cpp_type,
                "__module__": cls.__module__,
                "__doc__": f"Specialized fixed-point field: {cpp_type}.",
            },
            overrides,
        )
        specialized = type(subclass_name, (cls,), specialized_attrs)
        cls._specializations[key] = specialized
        return specialized

    @classmethod
    def init_value(cls) -> Any:
        return 0.0

    # --- value <-> bits (quantization), delegated to fixputils for bit-exactness ---
    def _convert(self, value: Any) -> Any:
        """Quantize the assigned real value to the nearest representable value.

        Returns the quantized float (``.val``). Idempotent on already-quantized
        values, so it is safe to call on a value reconstructed from bits."""
        cls = self.__class__
        stored = fixputils.quantize(
            value, cls.bitwidth, cls.int_bits, cls.signed, cls.q_mode, cls.o_mode)
        return fixputils.to_float(stored, cls.bitwidth, cls.int_bits)

    def _value_to_field_bits(self, current_val: Any) -> int:
        """The signed stored integer behind the quantized ``.val`` (serialize side)."""
        cls = self.__class__
        stored = fixputils.quantize(
            current_val, cls.bitwidth, cls.int_bits, cls.signed, cls.q_mode, cls.o_mode)
        return int(stored)

    def _field_bits_to_value(self, field_bits: int) -> Any:
        """Reconstruct the quantized float from the raw W-bit pattern (deserialize side)."""
        cls = self.__class__
        stored = fixputils.truncate(int(field_bits), cls.bitwidth, cls.signed)
        return fixputils.to_float(int(stored), cls.bitwidth, cls.int_bits)

    # --- C++ codegen: ap_fixed <-> ap_uint<W> word payload is a *bit-reinterpret*
    # (.range()), not a value cast — unlike ap_int. Route through the streamutils
    # fixed_to_bits / bits_to_fixed helpers (the same shape FloatField uses for its
    # float<->uint reinterpret). The W-bit payload itself is identical to IntField.
    @classmethod
    def to_uint_expr(cls, value_expr: str) -> str:
        return f"streamutils::fixed_to_bits<{cls.cpp_type}>({value_expr})"

    @classmethod
    def to_uint_value_expr(cls, value_expr: str) -> str:
        return f"streamutils::fixed_to_bits<{cls.cpp_type}>({value_expr})"

    @classmethod
    def from_uint_expr(cls, uint_expr: str) -> str:
        return f"streamutils::bits_to_fixed<{cls.cpp_type}>({uint_expr})"
