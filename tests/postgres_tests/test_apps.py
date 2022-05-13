import unittest
from decimal import Decimal

from django.db import connection
from django.db.backends.postgresql.base import PSYCOPG_VERSION
from django.db.backends.signals import connection_created
from django.db.migrations.writer import MigrationWriter
from django.test import TestCase
from django.test.utils import modify_settings

try:
    from django.contrib.postgres.fields import (
        DateRangeField,
        DateTimeRangeField,
        DecimalRangeField,
        IntegerRangeField,
    )
    from django.db.backends.postgresql.psycopg_any import (
        DateRange,
        DateTimeRange,
        DateTimeTZRange,
        NumericRange,
    )
except ImportError:
    pass


@unittest.skipUnless(connection.vendor == "postgresql", "PostgreSQL specific tests")
class PostgresConfigTests(TestCase):
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

    def test_register_serializer_for_migrations(self):
        tests = (
            (DateRange(empty=True), DateRangeField),
            (DateTimeRange(empty=True), DateRangeField),
            (DateTimeTZRange(None, None, "[]"), DateTimeRangeField),
            (NumericRange(Decimal("1.0"), Decimal("5.0"), "()"), DecimalRangeField),
            (NumericRange(1, 10), IntegerRangeField),
        )

        def assertNotSerializable():
            for default, test_field in tests:
                with self.subTest(default=default):
                    field = test_field(default=default)
                    with self.assertRaisesMessage(
                        ValueError, "Cannot serialize: %s" % default.__class__.__name__
                    ):
                        MigrationWriter.serialize(field)

        assertNotSerializable()
        import_name = (
            "psycopg2.extras" if PSYCOPG_VERSION[0] < 3 else "psycopg.types.range"
        )
        with self.modify_settings(INSTALLED_APPS={"append": "django.contrib.postgres"}):
            for default, test_field in tests:
                with self.subTest(default=default):
                    field = test_field(default=default)
                    serialized_field, imports = MigrationWriter.serialize(field)
                    self.assertEqual(
                        imports,
                        {
                            "import django.contrib.postgres.fields.ranges",
                            f"import {import_name}",
                        },
                    )
                    self.assertIn(
                        f"%s.%s(default={import_name}.%r)"
                        % (
                            field.__module__,
                            field.__class__.__name__,
                            default,
                        ),
                        serialized_field,
                    )
        assertNotSerializable()
