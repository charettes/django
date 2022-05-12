import unittest
from decimal import Decimal

from django.contrib.postgres.fields import (
    DateRangeField,
    DateTimeRangeField,
    DecimalRangeField,
    IntegerRangeField,
)
from django.db import connection
from django.db.backends.signals import connection_created
from django.db.migrations.writer import MigrationWriter
from django.test.utils import modify_settings

from . import PostgreSQLTestCase

ranges2 = ranges3 = None
try:
    from psycopg.types import range as ranges3
except ImportError:
    from psycopg2 import extras as ranges2


class PostgresConfigTests(PostgreSQLTestCase):
    def test_register_type_handlers_connection(self):
        from django.contrib.postgres.signals import register_type_handlers

        self.assertNotIn(
            register_type_handlers, connection_created._live_receivers(None)
        )
        with modify_settings(INSTALLED_APPS={"append": "django.contrib.postgres"}):
            self.assertIn(
                register_type_handlers, connection_created._live_receivers(None)
            )
        self.assertNotIn(
            register_type_handlers, connection_created._live_receivers(None)
        )

    @unittest.skipIf(
        connection.psycopg_version[0] >= 3, "TODO: psycopg3 migrations not implemented"
    )
    def test_register_serializer_for_migrations(self):
        if connection.psycopg_version[0] < 3:
            tests = [
                (ranges2.DateRange(empty=True), DateRangeField),
                (ranges2.DateTimeRange(empty=True), DateRangeField),
                (ranges2.DateTimeTZRange(None, None, "[]"), DateTimeRangeField),
                (
                    ranges2.NumericRange(Decimal("1.0"), Decimal("5.0"), "()"),
                    DecimalRangeField,
                ),
                (ranges2.NumericRange(1, 10), IntegerRangeField),
            ]
        else:
            tests = [
                (ranges3.Range(1, 10), IntegerRangeField),
            ]

        def assertNotSerializable():
            for default, test_field in tests:
                with self.subTest(default=default):
                    field = test_field(default=default)
                    with self.assertRaisesMessage(
                        ValueError, "Cannot serialize: %s" % default.__class__.__name__
                    ):
                        MigrationWriter.serialize(field)

        assertNotSerializable()
        with self.modify_settings(INSTALLED_APPS={"append": "django.contrib.postgres"}):
            for default, test_field in tests:
                with self.subTest(default=default):
                    field = test_field(default=default)
                    serialized_field, imports = MigrationWriter.serialize(field)
                    self.assertEqual(
                        imports,
                        {
                            "import django.contrib.postgres.fields.ranges",
                            "import psycopg2.extras",
                        },
                    )
                    self.assertIn(
                        "%s.%s(default=psycopg2.extras.%r)"
                        % (
                            field.__module__,
                            field.__class__.__name__,
                            default,
                        ),
                        serialized_field,
                    )
        assertNotSerializable()
