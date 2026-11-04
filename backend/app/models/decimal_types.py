from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import BigInteger, Numeric
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine


class ExactDecimal(TypeDecorator[Decimal]):
    impl = Numeric(18, 6)
    cache_ok = True

    _quantum = Decimal('0.000001')
    _maximum = Decimal('999999999999.999999')
    _sqlite_scale = 1_000_000
    _sqlite_minimum = -(2**63)
    _sqlite_maximum = 2**63 - 1

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine:
        if dialect.name == 'sqlite':
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(Numeric(18, 6))

    def process_bind_param(
        self,
        value: Optional[Decimal],
        dialect: Dialect,
    ) -> Optional[Decimal | int]:
        if value is None:
            return None

        quantized = self._quantize(value)
        if dialect.name == 'sqlite':
            scaled = int(quantized * self._sqlite_scale)
            if not self._sqlite_minimum <= scaled <= self._sqlite_maximum:
                raise ValueError(
                    'decimal value exceeds SQLite signed 64-bit integer range '
                    'after scaling to six decimal places'
                )
            return scaled
        return quantized

    def process_result_value(
        self,
        value: Optional[Decimal | int],
        dialect: Dialect,
    ) -> Optional[Decimal]:
        if value is None:
            return None
        if dialect.name == 'sqlite':
            return Decimal(value).scaleb(-6)
        return self._quantize(value)

    @classmethod
    def _quantize(cls, value: Decimal | str) -> Decimal:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if not decimal_value.is_finite():
            raise ValueError('decimal value must be finite')
        try:
            quantized = decimal_value.quantize(cls._quantum, rounding=ROUND_HALF_UP)
        except InvalidOperation as exc:
            raise ValueError('decimal value exceeds Numeric(18, 6)') from exc
        if abs(quantized) > cls._maximum:
            raise ValueError('decimal value exceeds Numeric(18, 6)')
        return quantized
