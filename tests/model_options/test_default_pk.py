import uuid

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models import UUIDField, DateTimeField
from django.db.models.functions import Now
from django.test import SimpleTestCase, override_settings
from django.test.utils import isolate_apps


class MyBigAutoField(models.BigAutoField):
    pass


class FieldWithDefault(UUIDField):
    def __init__(self, **kwargs):
        kwargs.setdefault("default", uuid.uuid4)
        super().__init__(**kwargs)


class FieldWithDBDefault(DateTimeField):
    def __init__(self, **kwargs):
        kwargs.setdefault("db_default", Now())
        super().__init__(**kwargs)


@isolate_apps("model_options")
class TestDefaultPK(SimpleTestCase):
    def test_default_value_of_default_pk_field_setting(self):
        """django.conf.global_settings defaults to BigAutoField."""

        class MyModel(models.Model):
            pass

        self.assertIsInstance(MyModel._meta.pk, models.BigAutoField)

    @override_settings(DEFAULT_PK_FIELD="django.db.models.NonexistentAutoField")
    def test_default_pk_field_setting_nonexistent(self):
        msg = (
            "DEFAULT_PK_FIELD refers to the module "
            "'django.db.models.NonexistentAutoField' that could not be "
            "imported."
        )
        with self.assertRaisesMessage(ImproperlyConfigured, msg):

            class Model(models.Model):
                pass

    @isolate_apps("model_options.apps.ModelPKNonexistentConfig")
    def test_app_default_pk_field_nonexistent(self):
        msg = (
            "model_options.apps.ModelPKNonexistentConfig.default_pk_field "
            "refers to the module 'django.db.models.NonexistentAutoField' "
            "that could not be imported."
        )
        with self.assertRaisesMessage(ImproperlyConfigured, msg):

            class Model(models.Model):
                pass

    @override_settings(DEFAULT_PK_FIELD="django.db.models.TextField")
    def test_default_pk_field_setting_non_default(self):
        msg = (
            "Primary key 'django.db.models.TextField' referred by "
            "DEFAULT_PK_FIELD must either define a `default` or a `db_default`."
        )
        with self.assertRaisesMessage(ValueError, msg):

            class Model(models.Model):
                pass

    @isolate_apps("model_options.apps.ModelPKNonAutoConfig")
    def test_app_default_pk_field_non_default(self):
        msg = (
            "Primary key 'django.db.models.TextField' referred by "
            "model_options.apps.ModelPKNonAutoConfig.default_pk_field "
            "must either define a `default` or a `db_default`."
        )
        with self.assertRaisesMessage(ValueError, msg):

            class Model(models.Model):
                pass

    @override_settings(DEFAULT_PK_FIELD=None)
    def test_default_pk_field_setting_none(self):
        msg = "DEFAULT_PK_FIELD must not be empty."
        with self.assertRaisesMessage(ImproperlyConfigured, msg):

            class Model(models.Model):
                pass

    @isolate_apps("model_options.apps.ModelPKNoneConfig")
    def test_app_default_pk_field_none(self):
        msg = (
            "model_options.apps.ModelPKNoneConfig.default_pk_field must not "
            "be empty."
        )
        with self.assertRaisesMessage(ImproperlyConfigured, msg):

            class Model(models.Model):
                pass

    @isolate_apps("model_options.apps.ModelDefaultPKConfig")
    @override_settings(DEFAULT_PK_FIELD="django.db.models.SmallAutoField")
    def test_default_pk_field_setting(self):
        class Model(models.Model):
            pass

        self.assertIsInstance(Model._meta.pk, models.SmallAutoField)

    @override_settings(
        DEFAULT_PK_FIELD="model_options.test_default_pk.MyBigAutoField"
    )
    def test_default_pk_field_setting_bigautofield_subclass(self):
        class Model(models.Model):
            pass

        self.assertIsInstance(Model._meta.pk, MyBigAutoField)

    @override_settings(
        DEFAULT_PK_FIELD="model_options.test_default_pk.FieldWithDefault"
    )
    def test_default_pk_field_setting_field_with_default(self):
        class Model(models.Model):
            pass

        self.assertIsInstance(Model._meta.pk, FieldWithDefault)

    @override_settings(
        DEFAULT_PK_FIELD="model_options.test_default_pk.FieldWithDBDefault"
    )
    def test_default_pk_field_setting_field_with_db_default(self):
        class Model(models.Model):
            pass

        self.assertIsInstance(Model._meta.pk, FieldWithDBDefault)

    @isolate_apps("model_options.apps.ModelPKConfig")
    @override_settings(DEFAULT_PK_FIELD="django.db.models.AutoField")
    def test_app_default_pk_field(self):
        class Model(models.Model):
            pass

        self.assertIsInstance(Model._meta.pk, models.SmallAutoField)

    @isolate_apps("model_options.apps.ModelDefaultPKConfig")
    @override_settings(DEFAULT_PK_FIELD="django.db.models.SmallAutoField")
    def test_m2m_default_pk_field_setting(self):
        class M2MModel(models.Model):
            m2m = models.ManyToManyField("self")

        m2m_pk = M2MModel._meta.get_field("m2m").remote_field.through._meta.pk
        self.assertIsInstance(m2m_pk, models.SmallAutoField)

    @isolate_apps("model_options.apps.ModelPKConfig")
    @override_settings(DEFAULT_PK_FIELD="django.db.models.AutoField")
    def test_m2m_app_default_pk_field(self):
        class M2MModel(models.Model):
            m2m = models.ManyToManyField("self")

        m2m_pk = M2MModel._meta.get_field("m2m").remote_field.through._meta.pk
        self.assertIsInstance(m2m_pk, models.SmallAutoField)
