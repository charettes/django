from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import connection
from django.db.models.query_utils import Q
from django.db.models.sql.query import Query

__all__ = ['CheckConstraint', 'UniqueConstraint']


class BaseConstraint:
    def __init__(self, name, auto_created=False):
        self.name = name
        self.auto_created = auto_created

    def constraint_sql(self, model, schema_editor):
        raise NotImplementedError('This method must be implemented by a subclass.')

    def create_sql(self, model, schema_editor):
        raise NotImplementedError('This method must be implemented by a subclass.')

    def remove_sql(self, model, schema_editor):
        raise NotImplementedError('This method must be implemented by a subclass.')

    def deconstruct(self):
        path = '%s.%s' % (self.__class__.__module__, self.__class__.__name__)
        path = path.replace('django.db.models.constraints', 'django.db.models')
        return (path, (), {'name': self.name})

    def clone(self):
        _, args, kwargs = self.deconstruct()
        return self.__class__(*args, **kwargs)

    def enforce(self, instance, exclude_fields=None):
        raise NotImplementedError


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

    def __repr__(self):
        return "<%s: check='%s' name=%r>" % (self.__class__.__name__, self.check, self.name)

    def __eq__(self, other):
        if isinstance(other, CheckConstraint):
            return self.name == other.name and self.check == other.check
        return super().__eq__(other)

    def deconstruct(self):
        path, args, kwargs = super().deconstruct()
        kwargs['check'] = self.check
        return path, args, kwargs


class UniqueConstraint(BaseConstraint):
    def __init__(self, *, fields, name, condition=None, auto_created=False):
        if not fields:
            raise ValueError('At least one field is required to define a unique constraint.')
        if not isinstance(condition, (type(None), Q)):
            raise ValueError('UniqueConstraint.condition must be a Q instance.')
        self.fields = tuple(fields)
        self.condition = condition
        super().__init__(name, auto_created)

    def _get_condition_sql(self, model, schema_editor):
        if self.condition is None:
            return None
        query = Query(model=model, alias_cols=False)
        where = query.build_where(self.condition)
        compiler = query.get_compiler(connection=schema_editor.connection)
        sql, params = where.as_sql(compiler, schema_editor.connection)
        return sql % tuple(schema_editor.quote_value(p) for p in params)

    def constraint_sql(self, model, schema_editor):
        fields = [model._meta.get_field(field_name).column for field_name in self.fields]
        condition = self._get_condition_sql(model, schema_editor)
        return schema_editor._unique_sql(model, fields, self.name, condition=condition)

    def create_sql(self, model, schema_editor):
        fields = [model._meta.get_field(field_name).column for field_name in self.fields]
        condition = self._get_condition_sql(model, schema_editor)
        return schema_editor._create_unique_sql(model, fields, self.name, condition=condition)

    def remove_sql(self, model, schema_editor):
        condition = self._get_condition_sql(model, schema_editor)
        return schema_editor._delete_unique_sql(model, self.name, condition=condition)

    def __repr__(self):
        return '<%s: fields=%r name=%r%s>' % (
            self.__class__.__name__, self.fields, self.name,
            '' if self.condition is None else ' condition=%s' % self.condition,
        )

    def __eq__(self, other):
        if isinstance(other, UniqueConstraint):
            return (
                self.name == other.name and
                self.fields == other.fields and
                self.condition == other.condition
            )
        return super().__eq__(other)

    def deconstruct(self):
        path, args, kwargs = super().deconstruct()
        kwargs['fields'] = self.fields
        if self.condition:
            kwargs['condition'] = self.condition
        return path, args, kwargs

    def enforce(self, instance, exclude_fields=None):
        if self.condition:
            return
        if exclude_fields and not exclude_fields.isdisjoint(self.fields):
            return

        opts = instance._meta
        model = opts.model
        lookup = {}
        for field_name in self.fields:
            field = opts.get_field(field_name)
            model = field.model
            lookup_value = getattr(instance, field.attname)
            # TODO: Handle multiple backends with different feature flags.
            if (lookup_value is None or
                    (lookup_value == '' and connection.features.interprets_empty_strings_as_nulls)):
                # no value, skip the lookup
                return
            if field.primary_key and not instance._state.adding:
                # no need to check for unique primary key when editing
                return
            lookup[field_name] = lookup_value

        queryset = model._default_manager.filter(**lookup)
        # Exclude the current object from the query if we are editing an
        # instance (as opposed to creating a new one)
        # Note that we need to use the pk as defined by opts.model, not
        # opts.pk. These can be different fields because model inheritance
        # allows single model to have effectively multiple primary keys.
        # Refs #17615.
        pk = instance._get_pk_val(opts)
        if not instance._state.adding and pk is not None:
            queryset = queryset.exclude(pk=pk)
        if not queryset.exists():
            return

        if len(self.fields) == 1:
            key = self.fields[0]
        else:
            key = NON_FIELD_ERRORS
        message = instance.unique_error_message(model, self.fields)
        raise ValidationError({key: message})
