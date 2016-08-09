from django.test import TestCase

from django.contrib.postgres.fields.jsonb import KeyTransform, KeyTextTransform

from .models import Foo


class JSONFTSTests(TestCase):
    def test_fts(self):
        obj = Foo.objects.create(data={'bar': 'test', 'baz': {'nested': 'test'}})
        qs = Foo.objects.annotate(
            bar=KeyTextTransform('bar', 'data'),
            baz=KeyTextTransform('nested', KeyTransform('baz', 'data')),
        )
        print(qs.query)
        found = qs.get(bar__contains='es')
        self.assertEqual(found, obj)
        self.assertEqual(found.bar, 'test')
        self.assertFalse(qs.filter(bar__contains='st"').exists())
        found = qs.get(baz__contains='es')
        self.assertEqual(found, obj)
        self.assertEqual(found.baz, 'test')
        self.assertFalse(qs.filter(baz__contains='st"').exists())
