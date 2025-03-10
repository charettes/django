from django.test import TestCase

from .models import Node
from .utils import RecursivePrefetch


class RecursivePrefetchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = Node.objects.create()
        cls.left = Node.objects.create(parent=cls.root)
        cls.right = Node.objects.create(parent=cls.root)
        cls.left_left = Node.objects.create(parent=cls.left)
        cls.left_right = Node.objects.create(parent=cls.left)
        cls.right_left = Node.objects.create(parent=cls.right)
        cls.right_right = Node.objects.create(parent=cls.right)

    def test_forward(self):
        nodes = Node.objects.prefetch_related(
            RecursivePrefetch(
                "parent",
                Node,
            )
        ).filter(id__in=[self.right_right.id, self.left_left.id])
        with self.assertNumQueries(2):
            for node in nodes:
                self.assertEqual(node.parent.parent, self.root)

    def test_reverse(self):
        nodes = Node.objects.prefetch_related(RecursivePrefetch("children", Node))
        with self.assertNumQueries(2):
            root = nodes.get(pk=self.root.pk)
            self.assertQuerySetEqual(root.children.all(), [self.left, self.right])
            left, right = root.children.all()
            self.assertQuerySetEqual(
                left.children.all(), [self.left_left, self.left_right]
            )
            self.assertQuerySetEqual(
                right.children.all(), [self.right_left, self.right_right]
            )
            left_left, left_right = left.children.all()
            right_left, right_right = right.children.all()
            self.assertQuerySetEqual(left_left.children.all(), [])
            self.assertQuerySetEqual(left_right.children.all(), [])
            self.assertQuerySetEqual(right_left.children.all(), [])
            self.assertQuerySetEqual(right_right.children.all(), [])

    def test_mixed(self):
        nodes = Node.objects.prefetch_related(
            RecursivePrefetch("parent", Node), RecursivePrefetch("children", Node)
        )
        with self.assertNumQueries(3):
            right = nodes.get(pk=self.right.pk)
            self.assertEqual(right.parent, self.root)
            self.assertQuerySetEqual(
                right.children.all(), [self.right_left, self.right_right]
            )
            right_left, right_right = right.children.all()
            self.assertQuerySetEqual(right_left.children.all(), [])
            self.assertQuerySetEqual(right_right.children.all(), [])
