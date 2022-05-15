"""
PostgreSQL database backend for Django.

Requires psycopg2 >= 2.8.4 or psycopg3
"""
import asyncio
import threading
import warnings
from contextlib import contextmanager
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError as WrappedDatabaseError
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorDebugWrapper as BaseCursorDebugWrapper
from django.db.utils import Text
from django.utils.asyncio import async_unsafe
from django.utils.functional import cached_property
from django.utils.safestring import SafeString
from django.utils.version import get_version_tuple

try:
    try:
        import psycopg as Database
    except ImportError:
        import psycopg2 as Database
except ImportError as e:
    raise ImproperlyConfigured("Error loading psycopg module: %s" % e)


def psycopg_version():
    version = Database.__version__.split(" ", 1)[0]
    return get_version_tuple(version)


PSYCOPG_VERSION = psycopg_version()


if PSYCOPG_VERSION < (2, 8, 4):
    raise ImproperlyConfigured(
        "psycopg2 version 2.8.4 or newer is required; you have %s"
        % Database.__version__
    )

if PSYCOPG_VERSION[0] >= 3:
    import psycopg
    from psycopg import sql
    from psycopg.pq import Format
    from psycopg.types.datetime import TimestamptzLoader
    from psycopg.types.range import Range, RangeDumper
    from psycopg.types.string import StrDumper, TextLoader

    TIMESTAMPTZ_OID = psycopg.adapters.types["timestamptz"].oid
    TSRANGE_OID = psycopg.postgres.types["tsrange"].oid
    TSTZRANGE_OID = psycopg.postgres.types["tstzrange"].oid
else:
    import psycopg2.extensions
    import psycopg2.extras

    psycopg2.extensions.register_adapter(SafeString, psycopg2.extensions.QuotedString)
    psycopg2.extras.register_uuid()

    # Register support for inet[] manually so we don't have to handle the Inet()
    # object on load all the time.
    INETARRAY_OID = 1041
    INETARRAY = psycopg2.extensions.new_array_type(
        (INETARRAY_OID,),
        "INETARRAY",
        psycopg2.extensions.UNICODE,
    )
    psycopg2.extensions.register_type(INETARRAY)

# Some of these import psycopg, so import them after checking if it's installed.
from .client import DatabaseClient  # NOQA isort:skip
from .creation import DatabaseCreation  # NOQA isort:skip
from .features import DatabaseFeatures  # NOQA isort:skip
from .introspection import DatabaseIntrospection  # NOQA isort:skip
from .operations import DatabaseOperations  # NOQA isort:skip
from .psycopg_any import IsolationLevel  # NOQA isort:skip
from .schema import DatabaseSchemaEditor  # NOQA isort:skip


@lru_cache
def get_adapters_template(use_tz, timezone):
    # Create at adapters map extending the base one to base connections on
    ctx = psycopg.adapt.AdaptersMap(psycopg.adapters)

    # Register a no-op dumper to avoid a round trip from psycopg3's decode
    # to json.dumps() to json.loads(), when using a custom decoder in
    # JSONField.
    ctx.register_loader("jsonb", TextLoader)

    # Don't convert automatically from Postgres network types to Python ipaddress
    ctx.register_loader("inet", TextLoader)
    ctx.register_loader("cidr", TextLoader)
    ctx.register_dumper(Range, DjangoRangeDumper)

    # Dump Text strings using the text oid, where the default unknown oid
    # doesn't work well (e.g. in variadic functions)
    ctx.register_dumper(Text, StrDumper)

    # Register a timestamptz loader configured on self.timezone.
    # This, however, can be overridden by create_cursor.
    register_tzloader(timezone, ctx)

    return ctx


class DatabaseWrapper(BaseDatabaseWrapper):
    vendor = "postgresql"
    display_name = "PostgreSQL"
    # This dictionary maps Field objects to their associated PostgreSQL column
    # types, as strings. Column-type strings can contain format strings; they'll
    # be interpolated against the values of Field.__dict__ before being output.
    # If a column type is set to None, it won't be included in the output.
    data_types = {
        "AutoField": "integer",
        "BigAutoField": "bigint",
        "BinaryField": "bytea",
        "BooleanField": "boolean",
        "CharField": "varchar(%(max_length)s)",
        "DateField": "date",
        "DateTimeField": "timestamp with time zone",
        "DecimalField": "numeric(%(max_digits)s, %(decimal_places)s)",
        "DurationField": "interval",
        "FileField": "varchar(%(max_length)s)",
        "FilePathField": "varchar(%(max_length)s)",
        "FloatField": "double precision",
        "IntegerField": "integer",
        "BigIntegerField": "bigint",
        "IPAddressField": "inet",
        "GenericIPAddressField": "inet",
        "JSONField": "jsonb",
        "OneToOneField": "integer",
        "PositiveBigIntegerField": "bigint",
        "PositiveIntegerField": "integer",
        "PositiveSmallIntegerField": "smallint",
        "SlugField": "varchar(%(max_length)s)",
        "SmallAutoField": "smallint",
        "SmallIntegerField": "smallint",
        "TextField": "text",
        "TimeField": "time",
        "UUIDField": "uuid",
    }
    data_type_check_constraints = {
        "PositiveBigIntegerField": '"%(column)s" >= 0',
        "PositiveIntegerField": '"%(column)s" >= 0',
        "PositiveSmallIntegerField": '"%(column)s" >= 0',
    }
    data_types_suffix = {
        "AutoField": "GENERATED BY DEFAULT AS IDENTITY",
        "BigAutoField": "GENERATED BY DEFAULT AS IDENTITY",
        "SmallAutoField": "GENERATED BY DEFAULT AS IDENTITY",
    }
    operators = {
        "exact": "= %s",
        "iexact": "= UPPER(%s)",
        "contains": "LIKE %s",
        "icontains": "LIKE UPPER(%s)",
        "regex": "~ %s",
        "iregex": "~* %s",
        "gt": "> %s",
        "gte": ">= %s",
        "lt": "< %s",
        "lte": "<= %s",
        "startswith": "LIKE %s",
        "endswith": "LIKE %s",
        "istartswith": "LIKE UPPER(%s)",
        "iendswith": "LIKE UPPER(%s)",
    }

    # The patterns below are used to generate SQL pattern lookup clauses when
    # the right-hand side of the lookup isn't a raw string (it might be an expression
    # or the result of a bilateral transformation).
    # In those cases, special characters for LIKE operators (e.g. \, *, _) should be
    # escaped on database side.
    #
    # Note: we use str.format() here for readability as '%' is used as a wildcard for
    # the LIKE operator.
    pattern_esc = (
        r"REPLACE(REPLACE(REPLACE({}, E'\\', E'\\\\'), E'%%', E'\\%%'), E'_', E'\\_')"
    )
    pattern_ops = {
        "contains": "LIKE '%%' || {} || '%%'",
        "icontains": "LIKE '%%' || UPPER({}) || '%%'",
        "startswith": "LIKE {} || '%%'",
        "istartswith": "LIKE UPPER({}) || '%%'",
        "endswith": "LIKE '%%' || {}",
        "iendswith": "LIKE '%%' || UPPER({})",
    }

    Database = Database
    SchemaEditorClass = DatabaseSchemaEditor
    # Classes instantiated in __init__().
    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations
    # PostgreSQL backend-specific attributes.
    _named_cursor_idx = 0

    # Map the initial connection state
    ctx_templates = {}

    def get_database_version(self):
        """
        Return a tuple of the database's version.
        E.g. for pg_version 120004, return (12, 4).
        """
        return divmod(self.pg_version, 10000)

    def get_connection_params(self):
        settings_dict = self.settings_dict
        # None may be used to connect to the default 'postgres' db
        if settings_dict["NAME"] == "" and not settings_dict.get("OPTIONS", {}).get(
            "service"
        ):
            raise ImproperlyConfigured(
                "settings.DATABASES is improperly configured. "
                "Please supply the NAME or OPTIONS['service'] value."
            )
        if len(settings_dict["NAME"] or "") > self.ops.max_name_length():
            raise ImproperlyConfigured(
                "The database name '%s' (%d characters) is longer than "
                "PostgreSQL's limit of %d characters. Supply a shorter NAME "
                "in settings.DATABASES."
                % (
                    settings_dict["NAME"],
                    len(settings_dict["NAME"]),
                    self.ops.max_name_length(),
                )
            )

        conn_params = {"client_encoding22": "UTF8"}
        if settings_dict["NAME"]:
            conn_params = {
                "dbname": settings_dict["NAME"],
                **settings_dict["OPTIONS"],
            }
        elif settings_dict["NAME"] is None:
            # Connect to the default 'postgres' db.
            settings_dict.get("OPTIONS", {}).pop("service", None)
            conn_params = {"dbname": "postgres", **settings_dict["OPTIONS"]}
        else:
            conn_params = {**settings_dict["OPTIONS"]}

        conn_params.pop("isolation_level", None)
        if settings_dict["USER"]:
            conn_params["user"] = settings_dict["USER"]
        if settings_dict["PASSWORD"]:
            conn_params["password"] = settings_dict["PASSWORD"]
        if settings_dict["HOST"]:
            conn_params["host"] = settings_dict["HOST"]
        if settings_dict["PORT"]:
            conn_params["port"] = settings_dict["PORT"]
        return conn_params

    @async_unsafe
    def get_new_connection(self, conn_params):
        if self.is_psycopg3:
            ctx = get_adapters_template(settings.USE_TZ, self.timezone)
            connection = Database.connect(**conn_params, context=ctx)
        else:
            connection = Database.connect(**conn_params)
            # Register dummy loads() to avoid a round trip from psycopg2's decode
            # to json.dumps() to json.loads(), when using a custom decoder in
            # JSONField.
            psycopg2.extras.register_default_jsonb(
                conn_or_curs=connection, loads=lambda x: x
            )

        # self.isolation_level must be set:
        # - after connecting to the database in order to obtain the database's
        #   default when no value is explicitly specified in options.
        # - before calling _set_autocommit() because if autocommit is on, that
        #   will set connection.isolation_level to ISOLATION_LEVEL_AUTOCOMMIT.
        options = self.settings_dict["OPTIONS"]
        try:
            isolevel = options["isolation_level"]
        except KeyError:
            self.isolation_level = IsolationLevel.READ_COMMITTED
        else:
            try:
                self.isolation_level = IsolationLevel(isolevel)
            except ValueError:
                raise ImproperlyConfigured(
                    "bad isolation_level: %s. Choose one of the "
                    "'psycopg.IsolationLevel' values" % (options["isolation_level"],)
                )
            connection.isolation_level = self.isolation_level

        connection.cursor_factory = Cursor

        return connection

    def ensure_timezone(self):
        if self.connection is None:
            return False

        conn_timezone_name = self.connection.info.parameter_status("TimeZone")
        timezone_name = self.timezone_name
        if timezone_name and conn_timezone_name != timezone_name:
            with self.connection.cursor() as cursor:
                cursor.execute(self.ops.set_time_zone_sql(), [timezone_name])
            return True
        return False

    def init_connection_state(self):
        super().init_connection_state()

        timezone_changed = self.ensure_timezone()
        if timezone_changed:
            # Commit after setting the time zone (see #17062)
            if not self.get_autocommit():
                self.connection.commit()

    @async_unsafe
    def create_cursor(self, name=None):
        if name:
            # In autocommit mode, the cursor will be used outside of a
            # transaction, hence use a holdable cursor.
            cursor = self.connection.cursor(
                name, scrollable=False, withhold=self.connection.autocommit
            )
        else:
            cursor = self.connection.cursor()

        if self.is_psycopg3:
            # Register the cursor timezone only if the connection disagrees, so that
            # we avoid to copy the adapters map.
            tzloader = self.connection.adapters.get_loader(TIMESTAMPTZ_OID, Format.TEXT)
            if self.timezone != tzloader.timezone:
                register_tzloader(self.timezone, cursor)
        else:
            cursor.tzinfo_factory = self.tzinfo_factory if settings.USE_TZ else None
        return cursor

    def tzinfo_factory(self, offset):
        return self.timezone

    @async_unsafe
    def chunked_cursor(self):
        self._named_cursor_idx += 1
        # Get the current async task
        # Note that right now this is behind @async_unsafe, so this is
        # unreachable, but in future we'll start loosening this restriction.
        # For now, it's here so that every use of "threading" is
        # also async-compatible.
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        # Current task can be none even if the current_task call didn't error
        if current_task:
            task_ident = str(id(current_task))
        else:
            task_ident = "sync"
        # Use that and the thread ident to get a unique name
        return self._cursor(
            name="_django_curs_%d_%s_%d"
            % (
                # Avoid reusing name in other threads / tasks
                threading.current_thread().ident,
                task_ident,
                self._named_cursor_idx,
            )
        )

    def _set_autocommit(self, autocommit):
        with self.wrap_database_errors:
            self.connection.autocommit = autocommit

    def check_constraints(self, table_names=None):
        """
        Check constraints by setting them to immediate. Return them to deferred
        afterward.
        """
        with self.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")

    def is_usable(self):
        try:
            # Use a psycopg cursor directly, bypassing Django's utilities.
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Database.Error:
            return False
        else:
            return True

    @contextmanager
    def _nodb_cursor(self):
        cursor = None
        try:
            with super()._nodb_cursor() as cursor:
                yield cursor
        except (Database.DatabaseError, WrappedDatabaseError):
            if cursor is not None:
                raise
            warnings.warn(
                "Normally Django will use a connection to the 'postgres' database "
                "to avoid running initialization queries against the production "
                "database when it's not needed (for example, when running tests). "
                "Django was unable to create a connection to the 'postgres' database "
                "and will use the first PostgreSQL database instead.",
                RuntimeWarning,
            )
            for connection in connections.all():
                if (
                    connection.vendor == "postgresql"
                    and connection.settings_dict["NAME"] != "postgres"
                ):
                    conn = self.__class__(
                        {
                            **self.settings_dict,
                            "NAME": connection.settings_dict["NAME"],
                        },
                        alias=self.alias,
                    )
                    try:
                        with conn.cursor() as cursor:
                            yield cursor
                    finally:
                        conn.close()
                    break
            else:
                raise

    @cached_property
    def is_psycopg3(self):
        return PSYCOPG_VERSION[0] >= 3

    @cached_property
    def pg_version(self):
        with self.temporary_connection():
            return self.connection.info.server_version

    def make_debug_cursor(self, cursor):
        return CursorDebugWrapper(cursor, self)


if PSYCOPG_VERSION[0] >= 3:

    class BaseTzLoader(TimestamptzLoader):
        """
        Load a Postgres timestamptz using the a specific timezone.

        The timezone can be None too, in which case it will be chopped.
        """

        timezone = None

        def load(self, data):
            res = super().load(data)
            return res.replace(tzinfo=self.timezone)

    def register_tzloader(tz, context):
        class SpecificTzLoader(BaseTzLoader):
            timezone = tz

        context.adapters.register_loader("timestamptz", SpecificTzLoader)

    class DjangoRangeDumper(RangeDumper):
        """
        A Range dumper customised for Django.
        """

        def upgrade(self, obj, format):
            # Dump ranges containing naive datetimes as tstzrange, because Django
            # doesn't use tz-aware ones.
            dumper = super().upgrade(obj, format)
            if dumper is not self and dumper.oid == TSRANGE_OID:
                dumper.oid = TSTZRANGE_OID
            return dumper

    class Cursor(Database.Cursor):
        """
        A subclass of psycopg cursor implementing callproc.
        """

        def callproc(self, name, args=None):
            if not isinstance(name, sql.Identifier):
                name = sql.Identifier(name)

            qparts = [sql.SQL("select * from "), name, sql.SQL("(")]
            if args:
                for item in args:
                    qparts.append(sql.Literal(item))
                    qparts.append(sql.SQL(","))
                del qparts[-1]

            qparts.append(sql.SQL(")"))
            stmt = sql.Composed(qparts)
            self.execute(stmt)
            return args

    class CursorDebugWrapper(BaseCursorDebugWrapper):
        def copy(self, statement):
            with self.debug_sql(statement):
                return self.cursor.copy(statement)

else:
    Cursor = psycopg2.extensions.cursor

    class CursorDebugWrapper(BaseCursorDebugWrapper):
        def copy_expert(self, sql, file, *args):
            with self.debug_sql(sql):
                return self.cursor.copy_expert(sql, file, *args)

        def copy_to(self, file, table, *args, **kwargs):
            with self.debug_sql(sql="COPY %s TO STDOUT" % table):
                return self.cursor.copy_to(file, table, *args, **kwargs)
