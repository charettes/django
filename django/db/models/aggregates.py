"""
Classes to represent the definitions of aggregate functions.
"""
from django.core.exceptions import FieldError
from django.db.models.expressions import Case, Func, Star, When
from django.db.models.fields import DecimalField, FloatField, IntegerField
from django.db.models.query_utils import Q

__all__ = [
    'Aggregate', 'Avg', 'Count', 'Max', 'Min', 'StdDev', 'Sum', 'Variance',
]


class Aggregate(Func):
    contains_aggregate = True
    name = None
    filter_template = '%s FILTER (WHERE %%(filter)s)'

    def __init__(self, *args, filter=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.filter = filter

    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        # Aggregates are not allowed in UPDATE queries, so ignore for_save
        c = super().resolve_expression(query, allow_joins, reuse, summarize)
        if not summarize:
            expressions = c.get_source_expressions()
            for index, expr in enumerate(expressions):
                if expr.contains_aggregate:
                    before_resolved = self.get_source_expressions()[index]
                    name = before_resolved.name if hasattr(before_resolved, 'name') else repr(before_resolved)
                    raise FieldError("Cannot compute %s('%s'): '%s' is an aggregate" % (c.name, name, name))
        if self.filter:
            c.filter = c.filter.resolve_expression(query, allow_joins, reuse, summarize, for_save)
        return c

    @property
    def default_alias(self):
        expressions = self.get_source_expressions()
        if len(expressions) == 1 and hasattr(expressions[0], 'name'):
            return '%s__%s' % (expressions[0].name, self.name.lower())
        raise TypeError("Complex expressions require an alias")

    def get_group_by_cols(self):
        return []

    def as_sql(self, compiler, connection, **extra_context):
        if self.filter:
            if connection.features.supports_aggregate_filter_clause:
                filter_sql, filter_params = self.filter.as_sql(compiler, connection)
                template = self.filter_template % extra_context.get('template', self.template)
                sql, params = super().as_sql(compiler, connection, template=template, filter=filter_sql)
                return sql, params + filter_params
            else:
                copy = self.copy()
                condition = When(Q())
                condition.set_source_expressions((self.filter, copy.get_source_expressions()[0]))
                copy.set_source_expressions([Case(condition)])
                return super(Aggregate, copy).as_sql(compiler, connection, **extra_context)

        return super().as_sql(compiler, connection, **extra_context)

    def __repr__(self):
        return "{}({}, filter={})".format(
            self.__class__.__name__,
            self.arg_joiner.join(str(arg) for arg in self.source_expressions),
            self.filter,
        )


class Avg(Aggregate):
    function = 'AVG'
    name = 'Avg'

    def _resolve_output_field(self):
        source_field = self.get_source_fields()[0]
        if isinstance(source_field, (IntegerField, DecimalField)):
            self._output_field = FloatField()
        super()._resolve_output_field()

    def as_oracle(self, compiler, connection):
        if self.output_field.get_internal_type() == 'DurationField':
            expression = self.get_source_expressions()[0]
            from django.db.backends.oracle.functions import IntervalToSeconds, SecondsToInterval
            return compiler.compile(
                SecondsToInterval(Avg(IntervalToSeconds(expression), filter=self.filter))
            )
        return super().as_sql(compiler, connection)


class Count(Aggregate):
    function = 'COUNT'
    name = 'Count'
    template = '%(function)s(%(distinct)s%(expressions)s)'

    def __init__(self, expression, distinct=False, filter=None, **extra):
        if expression == '*':
            expression = Star()

        if isinstance(expression, Star) and filter is not None:
            raise ValueError('Star cannot be used with filter. Please specify a field.')

        super().__init__(
            expression, distinct='DISTINCT ' if distinct else '',
            output_field=IntegerField(), filter=filter, **extra
        )

    def __repr__(self):
        return "{}({}, distinct={})".format(
            self.__class__.__name__,
            self.arg_joiner.join(str(arg) for arg in self.source_expressions),
            'False' if self.extra['distinct'] == '' else 'True',
        )

    def convert_value(self, value, expression, connection, context):
        if value is None:
            return 0
        return int(value)


class Max(Aggregate):
    function = 'MAX'
    name = 'Max'


class Min(Aggregate):
    function = 'MIN'
    name = 'Min'


class StdDev(Aggregate):
    name = 'StdDev'

    def __init__(self, expression, sample=False, **extra):
        self.function = 'STDDEV_SAMP' if sample else 'STDDEV_POP'
        super().__init__(expression, output_field=FloatField(), **extra)

    def __repr__(self):
        return "{}({}, sample={})".format(
            self.__class__.__name__,
            self.arg_joiner.join(str(arg) for arg in self.source_expressions),
            'False' if self.function == 'STDDEV_POP' else 'True',
        )

    def convert_value(self, value, expression, connection, context):
        if value is None:
            return value
        return float(value)


class Sum(Aggregate):
    function = 'SUM'
    name = 'Sum'

    def as_oracle(self, compiler, connection):
        if self.output_field.get_internal_type() == 'DurationField':
            expression = self.get_source_expressions()[0]
            from django.db.backends.oracle.functions import IntervalToSeconds, SecondsToInterval
            return compiler.compile(
                SecondsToInterval(Sum(IntervalToSeconds(expression)))
            )
        return super().as_sql(compiler, connection)


class Variance(Aggregate):
    name = 'Variance'

    def __init__(self, expression, sample=False, **extra):
        self.function = 'VAR_SAMP' if sample else 'VAR_POP'
        super().__init__(expression, output_field=FloatField(), **extra)

    def __repr__(self):
        return "{}({}, sample={})".format(
            self.__class__.__name__,
            self.arg_joiner.join(str(arg) for arg in self.source_expressions),
            'False' if self.function == 'VAR_POP' else 'True',
        )

    def convert_value(self, value, expression, connection, context):
        if value is None:
            return value
        return float(value)
