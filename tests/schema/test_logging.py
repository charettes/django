from unittest import skipIf, skipUnless

from django.db import connection
from django.test import TestCase


class SchemaLoggerTests(TestCase):
    @skipIf(
        connection.vendor == "postgresql" and connection.psycopg_version[0] > 2,
        reason="psycopg3 will merge on the client",
    )
    def test_extra_args(self):
        editor = connection.schema_editor(collect_sql=True)
        sql = "SELECT * FROM foo WHERE id in (%s, %s)"
        params = [42, 1337]
        with self.assertLogs("django.db.backends.schema", "DEBUG") as cm:
            editor.execute(sql, params)
        self.assertEqual(cm.records[0].sql, sql)
        self.assertEqual(cm.records[0].params, params)
        self.assertEqual(
            cm.records[0].getMessage(),
            "SELECT * FROM foo WHERE id in (%s, %s); (params [42, 1337])",
        )

    @skipUnless(
        connection.vendor == "postgresql" and connection.psycopg_version[0] > 2,
        reason="psycopg 3+ test",
    )
    def test_extra_args_psycopg3(self):
        editor = connection.schema_editor(collect_sql=True)
        sql = "SELECT * FROM foo WHERE id in (%s, %s)"
        params = [42, 1337]
        with self.assertLogs("django.db.backends.schema", "DEBUG") as cm:
            editor.execute(sql, params)
        merged = "SELECT * FROM foo WHERE id in (42, 1337)"
        self.assertEqual(cm.records[0].sql, merged)
        self.assertEqual(cm.records[0].params, None)
        self.assertEqual(cm.records[0].getMessage(), "%s; (params None)" % merged)
