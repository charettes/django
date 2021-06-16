from enum import Enum

from django.db import connections
from django.core.exceptions import FieldError, ValidationError
from django.db.models.expressions import (
    Exists, ExpressionList, ExpressionWrapper, F, Value,
)
from django.db.models.fields import BooleanField
from django.db.models.indexes import IndexExpression
from django.db.models.query_utils import Q
from django.db.models.sql.constants import SINGLE
from django.db.models.sql.query import Query
from django.db.utils import DEFAULT_DB_ALIAS
from django.utils.version import PY310

__all__ = ['CheckConstraint', 'Deferrable', 'UniqueConstraint']


class BaseConstraint:
    def __init__(self, name):
        self.name = name

    @property
    def contains_expressions(self):
        return False

    def constraint_sql(self, model, schema_editor):
        raise NotImplementedError('This method must be implemented by a subclass.')

    def create_sql(self, model, schema_editor):
        raise NotImplementedError('This method must be implemented by a subclass.')

    def remove_sql(self, model, schema_editor):
        raise NotImplementedError('This method must be implemented by a subclass.')

    def validate(self, instance, exclude=None, using=DEFAULT_DB_ALIAS):
        pass

    def deconstruct(self):
        path = '%s.%s' % (self.__class__.__module__, self.__class__.__name__)
        path = path.replace('django.db.models.constraints', 'django.db.models')
        return (path, (), {'name': self.name})

    def clone(self):
        _, args, kwargs = self.deconstruct()
        return self.__class__(*args, **kwargs)


class CheckConstraint(BaseConstraint):
    def __init__(self, *, check, name):
        self.check = check
        if not getattr(check, 'conditional', False):
            raise TypeError(
                'CheckConstraint.check must be a Q instance or boolean '
                'expression.'
            )
        super().__init__(name)

    def _get_check_sql(self, model, schema_editor):
        query = Query(model=model, alias_cols=False)
        where = query.build_where(self.check)
        compiler = query.get_compiler(connection=schema_editor.connection)
        sql, params = where.as_sql(compiler, schema_editor.connection)
        return sql % tuple(schema_editor.quote_value(p) for p in params)

    def constraint_sql(self, model, schema_editor):
        check = self._get_check_sql(model, schema_editor)
        return schema_editor._check_sql(self.name, check)

    def create_sql(self, model, schema_editor):
        check = self._get_check_sql(model, schema_editor)
        return schema_editor._create_check_sql(model, self.name, check)

    def remove_sql(self, model, schema_editor):
        return schema_editor._delete_check_sql(model, self.name)

    def validate(self, instance, exclude=None, using=DEFAULT_DB_ALIAS):
        query = Query(None)
        for field in instance._meta.local_concrete_fields:
            if exclude and field.name in exclude:
                continue
            value = getattr(instance, field.attname)
            query.add_annotation(Value(value, field), field.name, select=False)
        try:
            query.add_annotation(ExpressionWrapper(self.check, BooleanField()), '_check')
        except FieldError:
            # Check is referencing an excluded field
            return
        compiler = query.get_compiler(using=using)
        valid = compiler.execute_sql(SINGLE)[0]
        if not valid:
            raise ValidationError(f'Constraint {self.name} is violated')

    def __repr__(self):
        return '<%s: check=%s name=%s>' % (
            self.__class__.__qualname__,
            self.check,
            repr(self.name),
        )

    def __eq__(self, other):
        if isinstance(other, CheckConstraint):
            return self.name == other.name and self.check == other.check
        return super().__eq__(other)

    def deconstruct(self):
        path, args, kwargs = super().deconstruct()
        kwargs['check'] = self.check
        return path, args, kwargs


class Deferrable(Enum):
    DEFERRED = 'deferred'
    IMMEDIATE = 'immediate'

    # A similar format is used in Python 3.10+.
    if not PY310:
        def __repr__(self):
            return '%s.%s' % (self.__class__.__qualname__, self._name_)


class UniqueConstraint(BaseConstraint):
    def __init__(
        self,
        *expressions,
        fields=(),
        name=None,
        condition=None,
        deferrable=None,
        include=None,
        opclasses=(),
    ):
        if not name:
            raise ValueError('A unique constraint must be named.')
        if not expressions and not fields:
            raise ValueError(
                'At least one field or expression is required to define a '
                'unique constraint.'
            )
        if expressions and fields:
            raise ValueError(
                'UniqueConstraint.fields and expressions are mutually exclusive.'
            )
        if not isinstance(condition, (type(None), Q)):
            raise ValueError('UniqueConstraint.condition must be a Q instance.')
        if condition and deferrable:
            raise ValueError(
                'UniqueConstraint with conditions cannot be deferred.'
            )
        if include and deferrable:
            raise ValueError(
                'UniqueConstraint with include fields cannot be deferred.'
            )
        if opclasses and deferrable:
            raise ValueError(
                'UniqueConstraint with opclasses cannot be deferred.'
            )
        if expressions and deferrable:
            raise ValueError(
                'UniqueConstraint with expressions cannot be deferred.'
            )
        if expressions and opclasses:
            raise ValueError(
                'UniqueConstraint.opclasses cannot be used with expressions. '
                'Use django.contrib.postgres.indexes.OpClass() instead.'
            )
        if not isinstance(deferrable, (type(None), Deferrable)):
            raise ValueError(
                'UniqueConstraint.deferrable must be a Deferrable instance.'
            )
        if not isinstance(include, (type(None), list, tuple)):
            raise ValueError('UniqueConstraint.include must be a list or tuple.')
        if not isinstance(opclasses, (list, tuple)):
            raise ValueError('UniqueConstraint.opclasses must be a list or tuple.')
        if opclasses and len(fields) != len(opclasses):
            raise ValueError(
                'UniqueConstraint.fields and UniqueConstraint.opclasses must '
                'have the same number of elements.'
            )
        self.fields = tuple(fields)
        self.condition = condition
        self.deferrable = deferrable
        self.include = tuple(include) if include else ()
        self.opclasses = opclasses
        self.expressions = tuple(
            F(expression) if isinstance(expression, str) else expression
            for expression in expressions
        )
        super().__init__(name)

    @property
    def contains_expressions(self):
        return bool(self.expressions)

    def _get_condition_sql(self, model, schema_editor):
        if self.condition is None:
            return None
        query = Query(model=model, alias_cols=False)
        where = query.build_where(self.condition)
        compiler = query.get_compiler(connection=schema_editor.connection)
        sql, params = where.as_sql(compiler, schema_editor.connection)
        return sql % tuple(schema_editor.quote_value(p) for p in params)

    def _get_index_expressions(self, model, schema_editor):
        if not self.expressions:
            return None
        index_expressions = []
        for expression in self.expressions:
            index_expression = IndexExpression(expression)
            index_expression.set_wrapper_classes(schema_editor.connection)
            index_expressions.append(index_expression)
        return ExpressionList(*index_expressions).resolve_expression(
            Query(model, alias_cols=False),
        )

    def constraint_sql(self, model, schema_editor):
        fields = [model._meta.get_field(field_name) for field_name in self.fields]
        include = [model._meta.get_field(field_name).column for field_name in self.include]
        condition = self._get_condition_sql(model, schema_editor)
        expressions = self._get_index_expressions(model, schema_editor)
        return schema_editor._unique_sql(
            model, fields, self.name, condition=condition,
            deferrable=self.deferrable, include=include,
            opclasses=self.opclasses, expressions=expressions,
        )

    def create_sql(self, model, schema_editor):
        fields = [model._meta.get_field(field_name) for field_name in self.fields]
        include = [model._meta.get_field(field_name).column for field_name in self.include]
        condition = self._get_condition_sql(model, schema_editor)
        expressions = self._get_index_expressions(model, schema_editor)
        return schema_editor._create_unique_sql(
            model, fields, self.name, condition=condition,
            deferrable=self.deferrable, include=include,
            opclasses=self.opclasses, expressions=expressions,
        )

    def remove_sql(self, model, schema_editor):
        condition = self._get_condition_sql(model, schema_editor)
        include = [model._meta.get_field(field_name).column for field_name in self.include]
        expressions = self._get_index_expressions(model, schema_editor)
        return schema_editor._delete_unique_sql(
            model, self.name, condition=condition, deferrable=self.deferrable,
            include=include, opclasses=self.opclasses, expressions=expressions,
        )

    def __repr__(self):
        return '<%s:%s%s%s%s%s%s%s>' % (
            self.__class__.__qualname__,
            '' if not self.fields else ' fields=%s' % repr(self.fields),
            '' if not self.expressions else ' expressions=%s' % repr(self.expressions),
            ' name=%s' % repr(self.name),
            '' if self.condition is None else ' condition=%s' % self.condition,
            '' if self.deferrable is None else ' deferrable=%r' % self.deferrable,
            '' if not self.include else ' include=%s' % repr(self.include),
            '' if not self.opclasses else ' opclasses=%s' % repr(self.opclasses),
        )

    def __eq__(self, other):
        if isinstance(other, UniqueConstraint):
            return (
                self.name == other.name and
                self.fields == other.fields and
                self.condition == other.condition and
                self.deferrable == other.deferrable and
                self.include == other.include and
                self.opclasses == other.opclasses and
                self.expressions == other.expressions
            )
        return super().__eq__(other)

    def deconstruct(self):
        path, args, kwargs = super().deconstruct()
        if self.fields:
            kwargs['fields'] = self.fields
        if self.condition:
            kwargs['condition'] = self.condition
        if self.deferrable:
            kwargs['deferrable'] = self.deferrable
        if self.include:
            kwargs['include'] = self.include
        if self.opclasses:
            kwargs['opclasses'] = self.opclasses
        return path, self.expressions, kwargs

    def validate(self, instance, exclude=None, using=DEFAULT_DB_ALIAS):
        queryset = instance.__class__._default_manager.using(using)
        if self.fields:
            lookup_kwargs = {}
            for field_name in self.fields:
                if exclude and field_name in exclude:
                    return
                field = instance._meta.get_field(field_name)
                lookup_value = getattr(instance, field.attname)
                if (lookup_value is None or
                        (lookup_value == '' and connections[using].features.interprets_empty_strings_as_nulls)):
                    # A composite constraint containing NULL value cannot caused
                    # a violation since NULL != NULL in SQL.
                    return
                lookup_kwargs[field.name] = lookup_value
            queryset = queryset.filter(**lookup_kwargs)
        else:
            replacement_map = {
                field.name: Value(getattr(instance, field.attname), field)
                for field in instance._meta.local_concrete_fields
                if not exclude or field.name not in exclude
            }
            # XXX: Could use filter(Exact(expr, expr.replace_reference(replacement_map)), ...) directly when #27021 lands.
            queryset = queryset.alias(**{
                f'expr_{idx}': expr
                for idx, expr in enumerate(self.expressions)
            }).filter(**{
                f'expr_{idx}': expr.replace_references(replacement_map)
                for idx, expr in enumerate(self.expressions)
            })
        if not instance._state.adding and instance.pk is not None:
            queryset = queryset.exclude(pk=instance.pk)
        if not self.condition:
            if queryset.exists():
                raise ValidationError(f'Constraint {self.name} is violated')
        else:
            query = Query(None)
            for field in instance._meta.local_concrete_fields:
                if exclude and field.name in exclude:
                    continue
                value = getattr(instance, field.attname)
                query.add_annotation(Value(value, field), field.name, select=False)
            try:
                query.add_annotation(ExpressionWrapper(
                    ~self.condition | ~Exists(queryset), BooleanField()
                ), '_check')
            except FieldError:
                # Condition is referencing an excluded field
                return
            compiler = query.get_compiler(using=using)
            valid = compiler.execute_sql(SINGLE)[0]
            if not valid:
                raise ValidationError(f'Constraint {self.name} is violated')
