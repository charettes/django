from django.db import models


class Node(models.Model):
    parent = models.ForeignKey(
        "self",
        models.SET_NULL,
        null=True,
        related_name="children",
    )

    class Meta:
        ordering = ("pk",)
