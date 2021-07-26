from datetime import date

from django.test import modify_settings

from django.db import connection

from . import PostgreSQLTestCase
from .models import (
    HStoreModel,
    IntegerArrayModel,
    NestedIntegerArrayModel,
    NullableIntegerArrayModel,
    OtherTypesArrayModel,
    RangesModel,
)


@modify_settings(INSTALLED_APPS={"append": "django.contrib.postgres"})
class BulkSaveTests(PostgreSQLTestCase):
    def test_bulk_update(self):
        if connection.psycopg_version[0] < 3:
            from psycopg2.extras import DateRange, NumericRange
        else:
            from psycopg.types.range import Range

            DateRange = NumericRange = Range

        test_data = [
            (IntegerArrayModel, "field", [], [1, 2, 3]),
            (NullableIntegerArrayModel, "field", [1, 2, 3], None),
            (NestedIntegerArrayModel, "field", [], [[1, 2, 3]]),
            (HStoreModel, "field", {}, {1: 2}),
            (RangesModel, "ints", None, NumericRange(lower=1, upper=10)),
            (
                RangesModel,
                "dates",
                None,
                DateRange(lower=date.today(), upper=date.today()),
            ),
            (OtherTypesArrayModel, "ips", [], ["1.2.3.4"]),
            (OtherTypesArrayModel, "json", [], [{"a": "b"}]),
        ]
        for Model, field, initial, new in test_data:
            with self.subTest(model=Model, field=field):
                instances = Model.objects.bulk_create(
                    Model(**{field: initial}) for _ in range(20)
                )
                for instance in instances:
                    setattr(instance, field, new)
                Model.objects.bulk_update(instances, [field])
                self.assertSequenceEqual(
                    Model.objects.filter(**{field: new}), instances
                )
