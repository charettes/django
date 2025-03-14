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
from functools import partial
from operator import attrgetter
from typing import Iterator

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
        sql, query_params = super().as_sql(*args, **kwargs)
        self.query.order_by, self.query.default_ordering = query_ordering
        cte_sql_parts = []
        cte_aliases = []
        params = []
        for recursive_query, recursive_alias in self.query.get_recursive_queries():
            recursive_query_sql, recursive_query_params = SQLCompiler(
                recursive_query,
                self.connection,
                self.using,
                self.elide_empty,
            ).as_sql(*args, **kwargs)
            quoted_recursive_alias = self.connection.ops.quote_name(recursive_alias)
            cte_sql_parts.append(
                f"{quoted_recursive_alias} AS ({sql} UNION {recursive_query_sql})"
            )
            cte_aliases.append(quoted_recursive_alias)
            params.extend(query_params + recursive_query_params)
        cte_sql = ", ".join(cte_sql_parts)
        if len(cte_aliases) == 1:
            cte_union_sql = cte_aliases[0]
        else:
            cte_union_sql = "(%s)" % " UNION ".join(
                f"SELECT * FROM {cte_alias}" for cte_alias in cte_aliases
            )
        if order_by := self.get_order_by():
            ordering = []
            order_by_params = []
            replacements = {alias: None for alias in self.query.alias_map}
            for order_by_expr, *_ in order_by:
                order_by_expr = order_by_expr.relabeled_clone(replacements)
                order_by_sql, order_by_params = self.compile(order_by_expr)
                ordering.append(order_by_sql)
                params.extend(order_by_params)
            order_by_sql = "ORDER BY %s" % ", ".join(ordering)
        else:
            order_by_sql = ""
        sql = f"""
        WITH {cte_sql}
        SELECT * FROM {cte_union_sql} {order_by_sql}
        """
        return sql, tuple(params)


class RecursiveQuery(Query):
    recursion_link: Field
    recursion_bidirectional: bool

    @property
    def recursion_links(self) -> list[Field]:
        if self.recursion_bidirectional:
            return [self.recursion_link, self.recursion_link.remote_field]
        return [self.recursion_link]

    def get_recursive_queries(self) -> Iterator[tuple[Query, str]]:
        query = self.clone()
        query.clear_where()
        query.clear_ordering(force=True)
        query.add_annotation(Value(False), PREFETCH_DIRECT_ATTR)
        base_alias = query.get_initial_alias()
        for recursion_link in self.recursion_links:
            recursive_query = query.clone()
            # Inject an alias to the CTE for the JOIN to reference it.
            recursive_query_alias = recursion_link.name
            recursive_query.alias_map[recursive_query_alias] = BaseTable(
                base_alias, recursive_query_alias
            )
            recursive_alias = recursive_query.join(
                Join(
                    recursive_query_alias,
                    base_alias,
                    table_alias=None,
                    join_type=INNER,
                    join_field=recursion_link.remote_field,
                    nullable=False,
                ),
                reuse=set(),
            )
            yield recursive_query, recursive_alias

    def get_compiler(self, using=None, connection=None, elide_empty=True):
        if using is None and connection is None:
            raise ValueError("Need either using or connection")
        if using:
            connection = connections[using]
        return RecursiveSQLCompiler(self, connection, using, elide_empty)


class _RecursiveModelIterable(ModelIterable):
    @staticmethod
    def _one_to_many_setter(get_manager, get_link, links, link_name, obj):
        link = get_link(obj)
        value = links[link]
        queryset = get_manager(obj).get_queryset()
        queryset._result_cache = value
        try:
            objects_cache = obj._prefetched_objects_cache
        except AttributeError:
            objects_cache = {}
            obj._prefetched_objects_cache = objects_cache
        objects_cache[link_name] = queryset

    @staticmethod
    def _many_to_one_setter(get_link, links, link_setter, obj):
        link = get_link(obj)
        try:
            value = links[link][0]
        except LookupError:
            # Field might be nullable.
            return
        link_setter(obj, value)

    def __iter__(self):
        recursion_links: list[Field] = self.queryset.query.recursion_links
        objs = []
        direct_objs = []
        links = {field: defaultdict(list) for field in recursion_links}
        link_getters = [
            (
                links[field],
                (
                    field.get_foreign_related_value
                    if field.many_to_one
                    else field.remote_field.get_local_related_value
                ),
            )
            for field in recursion_links
        ]
        for obj in super().__iter__():
            for field_links, get_link in link_getters:
                link = get_link(obj)
                field_links[link].append(obj)
            objs.append(obj)
            if obj.__dict__.pop(PREFETCH_DIRECT_ATTR):
                direct_objs.append(obj)
        link_setters = [
            (
                partial(
                    self._one_to_many_setter,
                    attrgetter(field.name),
                    field.remote_field.get_foreign_related_value,
                    links[field],
                    field.name,
                )
                if field.one_to_many
                else partial(
                    self._many_to_one_setter,
                    field.get_local_related_value,
                    links[field],
                    field.set_cached_value,
                )
            )
            for field in recursion_links
        ]
        for obj in objs:
            for link_setter in link_setters:
                link_setter(obj)
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

    def __init__(self, lookup: str, model: Model, *, bidirectional: bool = False):
        # XXX: Custom queryset and to_attr support could be implemented but it
        # would require a few tweaks.
        # XXX: bidirectional=True is broken for direct reverse relatioship of
        # top level objects as prefetched relationships assignment is not
        # delegated to Prefetch so only one direction will be assigned by the
        # prefetching machinery even if all rows are fetched.
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
        recursive_queryset.query.recursion_bidirectional = bidirectional
        recursive_queryset._iterable_class = _RecursiveModelIterable
        super().__init__(lookup, recursive_queryset)
