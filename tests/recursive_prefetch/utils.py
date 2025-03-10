"""
Copyright (c) 2025, Simon Charette

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from __future__ import annotations

from collections import defaultdict
from operator import attrgetter

from django.core.exceptions import FieldError
from django.db import connections
from django.db.models import Field, Model, Prefetch, Value
from django.db.models.query import ModelIterable
from django.db.models.sql.compiler import SQLCompiler
from django.db.models.sql.constants import INNER
from django.db.models.sql.datastructures import BaseTable, Join
from django.db.models.sql.query import Query

PREFETCH_DIRECT_ATTR = "_recursive_prefetch_direct"


class RecursiveSQLCompiler(SQLCompiler):
    query: RecursiveQuery

    def as_sql(self, *args, **kwargs):
        # XXX: Since SQLCompiler.as_sql doesn't have a with_order_by flag
        # to exclude the ORDER BY clause it must be temporarily pruned from
        # the query to be added to the outer one.
        query_ordering = self.query.order_by, self.query.default_ordering
        self.query.clear_ordering(force=True)
        sql, params = super().as_sql(*args, **kwargs)
        self.query.order_by, self.query.default_ordering = query_ordering
        recursive_query, recursive_alias = self.query.get_recursive_query()
        quoted_recursive_alias = self.connection.ops.quote_name(recursive_alias)
        recursive_sql, recursive_params = SQLCompiler(
            recursive_query,
            self.connection,
            self.using,
            self.elide_empty,
        ).as_sql(*args, **kwargs)
        if order_by := self.get_order_by():
            ordering = []
            order_by_params = []
            replacements = {alias: recursive_alias for alias in self.query.alias_map}
            for order_by_expr, *_ in order_by:
                order_by_expr = order_by_expr.relabeled_clone(replacements)
                order_by_sql, order_by_params = self.compile(order_by_expr)
                ordering.append(order_by_sql)
                order_by_params.extend(order_by_params)
            order_by_sql = " ORDER BY %s" % ", ".join(ordering)
        else:
            order_by_sql = ""
            order_by_params = ()
        sql = f"""
        WITH RECURSIVE {quoted_recursive_alias} AS (
            {sql}
            UNION
            {recursive_sql}
        )
        SELECT * FROM {quoted_recursive_alias}{order_by_sql}
        """
        return sql, (*params, *recursive_params, *order_by_params)


class RecursiveQuery(Query):
    recursion_link: Field
    recursive_cte_alias = "recursive_cte"

    def get_recursive_query(self) -> tuple[Query, str]:
        query = self.clone()
        query.clear_where()
        query.clear_ordering(force=True)
        base_alias = query.get_initial_alias()
        # Inject an alias to the CTE for the JOIN to reference it.
        query.alias_map["recursive_cte"] = BaseTable(
            base_alias, self.recursive_cte_alias
        )
        query.add_annotation(Value(False), PREFETCH_DIRECT_ATTR)
        recursive_alias = query.join(
            Join(
                self.recursive_cte_alias,
                base_alias,
                table_alias=None,
                join_type=INNER,
                join_field=self.recursion_link.remote_field,
                nullable=False,
            ),
            reuse=set(),
        )
        return query, recursive_alias

    def get_compiler(self, using=None, connection=None, elide_empty=True):
        if using is None and connection is None:
            raise ValueError("Need either using or connection")
        if using:
            connection = connections[using]
        return RecursiveSQLCompiler(self, connection, using, elide_empty)


class _RecursiveModelIterable(ModelIterable):
    def __iter__(self):
        recursion_link: Field = self.queryset.query.recursion_link
        objs = []
        direct_objs = []
        links = defaultdict(list)
        if recursion_link.many_to_one:
            get_foreign_link = recursion_link.get_foreign_related_value
            get_local_link = recursion_link.get_local_related_value
        else:
            get_foreign_link = recursion_link.remote_field.get_local_related_value
            get_local_link = recursion_link.remote_field.get_foreign_related_value
        for obj in super().__iter__():
            link = get_foreign_link(obj)
            links[link].append(obj)
            objs.append(obj)
            if obj.__dict__.pop(PREFETCH_DIRECT_ATTR):
                direct_objs.append(obj)
        link_name = recursion_link.name
        if recursion_link.one_to_many:
            get_manager = attrgetter(link_name)
            for obj in objs:
                link = get_local_link(obj)
                value = links[link]
                queryset = get_manager(obj).get_queryset()
                queryset._result_cache = value
                try:
                    objects_cache = obj._prefetched_objects_cache
                except AttributeError:
                    objects_cache = {}
                    obj._prefetched_objects_cache = objects_cache
                objects_cache[link_name] = queryset
        else:
            for obj in objs:
                link = get_local_link(obj)
                try:
                    value = links[link][0]
                except LookupError:
                    # Field might be nullable.
                    continue
                setattr(obj, link_name, value)
        yield from direct_objs


class RecursivePrefetch(Prefetch):
    """
    Implement efficient prefetching of recursive many-to-one and one-to-many
    relationships.

    The first argument should be the anchor relationship and the second the
    the model defining the relationship.

    Note that the model is unfortunately required as the prefetch machinery
    doesn't make use of a Prefetch entrypoint so it can resolve its queryset
    from its lookup.
    """

    def __init__(self, lookup: str, model: Model):
        # XXX: Custom queryset and to_attr support could be implemented but it
        # would require a few tweaks.
        queryset = model._base_manager.all()
        field = model._meta.get_field(lookup)
        if (
            not (field.many_to_one or field.one_to_many)
            or not field.related_model is field.remote_field.related_model
        ):
            raise FieldError(
                "RecursivePrefetch only supports many-to-one and many-to-many "
                "recursive relationships."
            )
        recursive_queryset = queryset._clone()
        recursive_queryset.query = queryset.query.chain(RecursiveQuery)
        recursive_queryset.query.recursion_link = field
        recursive_queryset.query.add_annotation(Value(True), PREFETCH_DIRECT_ATTR)
        recursive_queryset._iterable_class = _RecursiveModelIterable
        super().__init__(lookup, recursive_queryset)
