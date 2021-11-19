from django.db.models.expressions import OrderByList


class OrderableAggMixin:

    def __init__(self, *expressions, ordering=(), **extra):
        self.order_by = (
            OrderByList(*ordering) if isinstance(ordering, (list, tuple)) else OrderByList(ordering)
        )
        super().__init__(*expressions, **extra)

    def get_source_expressions(self):
        return super().get_source_expressions() + [self.order_by]

    def set_source_expressions(self, exprs):
        *exprs, self.order_by = exprs
        return super().set_source_expressions(exprs)

    def as_sql(self, compiler, connection):
        order_by_sql, order_by_params = compiler.compile(self.order_by)
        sql, sql_params = super().as_sql(compiler, connection, ordering=order_by_sql)
        return sql, (*sql_params, *order_by_params)
