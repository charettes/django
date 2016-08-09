from django.db import models
from django.contrib.postgres.fields import JSONField


class Foo(models.Model):
    data = JSONField()
