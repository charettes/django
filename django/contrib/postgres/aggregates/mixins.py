class OrderableAggMixin:
    allow_order_by = True

    def __init__(self, *expressions, ordering=(), **extra):
        if ordering:
            # XXX: Deprecation warning.
            extra["order_by"] = ordering
        super().__init__(*expressions, **extra)
