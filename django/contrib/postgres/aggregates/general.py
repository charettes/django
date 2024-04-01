from django.contrib.postgres.fields import ArrayField
from django.db.models import Aggregate, BooleanField, JSONField, TextField, Value
from django.db.models.aggregates import StringAgg as _StringAgg

from .mixins import OrderableAggMixin

__all__ = [
    "ArrayAgg",
    "BitAnd",
    "BitOr",
    "BitXor",
    "BoolAnd",
    "BoolOr",
    "JSONBAgg",
    "StringAgg",
]


class ArrayAgg(OrderableAggMixin, Aggregate):
    function = "ARRAY_AGG"
    allow_distinct = True

    @property
    def output_field(self):
        return ArrayField(self.source_expressions[0].output_field)


class BitAnd(Aggregate):
    function = "BIT_AND"


class BitOr(Aggregate):
    function = "BIT_OR"


class BitXor(Aggregate):
    function = "BIT_XOR"


class BoolAnd(Aggregate):
    function = "BOOL_AND"
    output_field = BooleanField()


class BoolOr(Aggregate):
    function = "BOOL_OR"
    output_field = BooleanField()


class JSONBAgg(OrderableAggMixin, Aggregate):
    function = "JSONB_AGG"
    allow_distinct = True
    output_field = JSONField()


class StringAgg(OrderableAggMixin, _StringAgg):
    def __init__(self, expression, delimiter, **extra):
        if isinstance(delimiter, str):
            # XXX: warnings.warn
            delimiter = Value(str(delimiter))
        super().__init__(expression, delimiter, **extra)
