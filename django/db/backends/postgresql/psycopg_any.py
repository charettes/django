import ipaddress

try:
    from psycopg import IsolationLevel, errors, sql
    from psycopg.types.range import Range

    Inet = ipaddress.ip_address

    DateRange = DateTimeRange = DateTimeTZRange = NumericRange = Range
    RANGE_TYPES = (Range,)

    is_psycopg3 = True

except ImportError:
    from enum import IntEnum

    from psycopg2 import errors, extensions, sql  # NOQA
    from psycopg2.extras import (  # NOQA
        DateRange,
        DateTimeRange,
        DateTimeTZRange,
        Inet,
        NumericRange,
        Range,
    )

    def _quote(value, cursor_or_connection):
        adapted = extensions.adapt(value)
        if hasattr(adapted, "encoding"):
            adapted.encoding = "utf8"
        # getquoted() returns a quoted bytestring of the adapted value.
        return adapted.getquoted().decode()

    sql.quote = _quote

    class IsolationLevel(IntEnum):
        READ_UNCOMMITTED = extensions.ISOLATION_LEVEL_READ_UNCOMMITTED
        READ_COMMITTED = extensions.ISOLATION_LEVEL_READ_COMMITTED
        REPEATABLE_READ = extensions.ISOLATION_LEVEL_REPEATABLE_READ
        SERIALIZABLE = extensions.ISOLATION_LEVEL_SERIALIZABLE

    RANGE_TYPES = (DateRange, DateTimeRange, DateTimeTZRange, NumericRange)

    is_psycopg3 = False
