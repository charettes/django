try:
    from psycopg import errors, sql
    from psycopg.types.range import Range

    DateRange = DateTimeRange = DateTimeTZRange = NumericRange = Range
except ImportError:
    from psycopg2 import errors, sql  # NOQA
    from psycopg2.extras import (  # NOQA
        DateRange,
        DateTimeRange,
        DateTimeTZRange,
        NumericRange,
    )
