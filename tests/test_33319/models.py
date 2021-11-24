# models.py
from django.db import models


class Foo(models.Model):
    qux = models.ForeignKey("Qux", on_delete=models.CASCADE, related_name="foos")


class Bar(models.Model):
    foo = models.ForeignKey("Foo", on_delete=models.CASCADE, related_name="bars")
    another_foo = models.ForeignKey("Foo", on_delete=models.CASCADE, related_name="other_bars")
    baz = models.ForeignKey("Baz", on_delete=models.CASCADE, related_name="bars")


class Baz(models.Model):
    pass


class Qux(models.Model):
    bazes = models.ManyToManyField("Baz", related_name="quxes")
