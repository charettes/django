# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class AssertionOperation(migrations.RunPython):
    def __init__(self, model_name):
        self.model_name = model_name
        super(AssertionOperation, self).__init__(self.assert_, self.assert_)


class AssertContentTypeDoesNotExists(AssertionOperation):
    def assert_(self, apps, schema_editor):
        ContentType = apps.get_model('contenttypes', 'ContentType')
        msg = "A ContentType for the contenttypes_tests.%s should not exists." % self.model_name
        assert not ContentType.objects.filter(app_label='contenttypes_tests', model=self.model_name).exists(), msg


class AssertContentTypeExists(AssertionOperation):
    def assert_(self, apps, schema_editor):
        ContentType = apps.get_model('contenttypes', 'ContentType')
        msg = "A ContentType for the contenttypes_tests.%s should exists." % self.model_name
        assert ContentType.objects.filter(app_label='contenttypes_tests', model=self.model_name).exists(), msg


class Migration(migrations.Migration):

    operations = [
        AssertContentTypeDoesNotExists('foo'),
        migrations.CreateModel(
            'Foo',
            [
                ('id', models.AutoField(primary_key=True)),
            ],
        ),
        AssertContentTypeExists('foo'),
        migrations.RenameModel('Foo', 'RenamedFoo'),
        AssertContentTypeDoesNotExists('foo'),
        AssertContentTypeExists('renamedfoo'),
        migrations.DeleteModel('RenamedFoo'),
        AssertContentTypeDoesNotExists('renamedfoo'),
    ]
