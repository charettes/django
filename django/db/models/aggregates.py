"""
Classes to represent the definitions of aggregate functions.
"""
from django.core.exceptions import FieldError
from django.db.models.constants import LOOKUP_SEP
from django.db.models.expressions import (
    Case, F, Func, OuterRef, Star, Subquery, When,
)
from django.db.models.fields import IntegerField
from django.db.models.functions.mixins import (
    FixDurationInputMixin, NumericOutputFieldMixin,
)

__all__ = [
    'Aggregate', 'Avg', 'Count', 'Max', 'Min', 'StdDev', 'Sum', 'Variance',
]


class Aggregate(Func):
    template = '%(function)s(%(distinct)s%(expressions)s)'
    contains_aggregate = True
    name = None
    filter_template = '%s FILTER (WHERE %%(filter)s)'
    window_compatible = True
    allow_distinct = False

    def __init__(self, *expressions, distinct=False, filter=None, as_subquery=False, **extra):
        if distinct and not self.allow_distinct:
            raise TypeError("%s does not allow distinct." % self.__class__.__name__)
        self.distinct = distinct
        self.filter = filter
        self.as_subquery = as_subquery or not self.filter
        super().__init__(*expressions, **extra)

    def get_source_fields(self):
        # Don't return the filter expression since it's not a source field.
        return [e._output_field_or_none for e in super().get_source_expressions()]

    def get_source_expressions(self):
        source_expressions = super().get_source_expressions()
        if self.filter:
            return source_expressions + [self.filter]
        return source_expressions

    def set_source_expressions(self, exprs):
        self.filter = self.filter and exprs.pop()
        return super().set_source_expressions(exprs)

    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        if self.as_subquery and not summarize:
            aggregate = self.copy()
            aggregate.as_subquery = False
            model = query.model
            manager = model._base_manager
            inner_attname = 'pk'
            outer_attname = 'pk'
            related_field = None
            for expr in self.source_expressions:
                if isinstance(expr, F):
                    field_name = expr.name.split(LOOKUP_SEP, 1)[0]
                    if field_name == 'pk':
                        break
                    if related_field is None:
                        field = model._meta.get_field(field_name)
                        if not field.remote_field:
                            break
                        related_field = field
                    elif related_field.name != field_name:
                        break
                # TODO: Account for filter as well.
            else:
                if related_field is not None:
                    path_info = related_field.get_path_info()
                    manager = path_info[0].to_opts.model._base_manager
                    remote_field = path_info[0].join_field.remote_field
                    inner_attname = remote_field.get_attname()
                    outer_attname = remote_field.target_field.get_attname()
                    repointed_field_name = LOOKUP_SEP.join(
                        path.join_field.name for path in path_info[1:]
                    )
                    aggregate.set_source_expressions([
                        F(repointed_field_name + expr.name[len(related_field.name):]) if isinstance(expr, F) else expr
                        for expr in self.source_expressions
                    ])
                    # TODO: Account for filter as well.
            subquery = manager.filter(**{
                inner_attname: OuterRef(outer_attname)
            }).values(inner_attname).annotate(
                _subagg=aggregate
            ).values('_subagg')
            return Subquery(subquery).resolve_expression(
                query, allow_joins, reuse, summarize, for_save
            )
        # Aggregates are not allowed in UPDATE queries, so ignore for_save
        c = super().resolve_expression(query, allow_joins, reuse, summarize)
        c.filter = c.filter and c.filter.resolve_expression(query, allow_joins, reuse, summarize)
        if not summarize:
            # Call Aggregate.get_source_expressions() to avoid
            # returning self.filter and including that in this loop.
            expressions = super(Aggregate, c).get_source_expressions()
            for index, expr in enumerate(expressions):
                if expr.contains_aggregate:
                    before_resolved = self.get_source_expressions()[index]
                    name = before_resolved.name if hasattr(before_resolved, 'name') else repr(before_resolved)
                    raise FieldError("Cannot compute %s('%s'): '%s' is an aggregate" % (c.name, name, name))
        return c

    @property
    def default_alias(self):
        expressions = self.get_source_expressions()
        if len(expressions) == 1 and hasattr(expressions[0], 'name'):
            return '%s__%s' % (expressions[0].name, self.name.lower())
        raise TypeError("Complex expressions require an alias")

    def get_group_by_cols(self, alias=None):
        return []

    def as_sql(self, compiler, connection, **extra_context):
        extra_context['distinct'] = 'DISTINCT ' if self.distinct else ''
        if self.filter:
            if connection.features.supports_aggregate_filter_clause:
                filter_sql, filter_params = self.filter.as_sql(compiler, connection)
                template = self.filter_template % extra_context.get('template', self.template)
                sql, params = super().as_sql(
                    compiler, connection, template=template, filter=filter_sql,
                    **extra_context
                )
                return sql, params + filter_params
            else:
                copy = self.copy()
                copy.filter = None
                source_expressions = copy.get_source_expressions()
                condition = When(self.filter, then=source_expressions[0])
                copy.set_source_expressions([Case(condition)] + source_expressions[1:])
                return super(Aggregate, copy).as_sql(compiler, connection, **extra_context)
        return super().as_sql(compiler, connection, **extra_context)

    def _get_repr_options(self):
        options = super()._get_repr_options()
        if self.distinct:
            options['distinct'] = self.distinct
        if self.filter:
            options['filter'] = self.filter
        if self.as_subquery:
            options['as_subquery'] = self.as_subquery
        return options


class Avg(FixDurationInputMixin, NumericOutputFieldMixin, Aggregate):
    function = 'AVG'
    name = 'Avg'
    allow_distinct = True


class Count(Aggregate):
    function = 'COUNT'
    name = 'Count'
    output_field = IntegerField()
    allow_distinct = True

    def __init__(self, expression, filter=None, **extra):
        if expression == '*':
            expression = Star()
        if isinstance(expression, Star) and filter is not None:
            raise ValueError('Star cannot be used with filter. Please specify a field.')
        super().__init__(expression, filter=filter, **extra)

    def convert_value(self, value, expression, connection):
        return 0 if value is None else value


class Max(Aggregate):
    function = 'MAX'
    name = 'Max'


class Min(Aggregate):
    function = 'MIN'
    name = 'Min'


class StdDev(NumericOutputFieldMixin, Aggregate):
    name = 'StdDev'

    def __init__(self, expression, sample=False, **extra):
        self.function = 'STDDEV_SAMP' if sample else 'STDDEV_POP'
        super().__init__(expression, **extra)

    def _get_repr_options(self):
        return {**super()._get_repr_options(), 'sample': self.function == 'STDDEV_SAMP'}


class Sum(FixDurationInputMixin, Aggregate):
    function = 'SUM'
    name = 'Sum'
    allow_distinct = True


class Variance(NumericOutputFieldMixin, Aggregate):
    name = 'Variance'

    def __init__(self, expression, sample=False, **extra):
        self.function = 'VAR_SAMP' if sample else 'VAR_POP'
        super().__init__(expression, **extra)

    def _get_repr_options(self):
        return {**super()._get_repr_options(), 'sample': self.function == 'VAR_SAMP'}
