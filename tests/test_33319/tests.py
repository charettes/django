# Failing tests
from django.db.models import Q

from .models import Foo, Qux, Baz

from django.test import TestCase


class Tests(TestCase):
    def test_foo(self):
        qux = Qux.objects.create()

        qs1 = qux.foos.all()
        qs2 = Foo.objects.filter(
            Q(bars__baz__in=Baz.objects.all()) | Q(other_bars__baz__in=Baz.objects.all())
        )

        # breakpoint()

        # # Works fine.
        print(str((qs2 | qs1).query))

        # breakpoint()

        # AssertionError
        # "/django/db/models/sql/query.py", line 854, in Query.change_aliases
        # change_map = {'T4': 'T5', 'T5': 'T6'}
        print(str((qs1 | qs2).query))

