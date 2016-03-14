from __future__ import unicode_literals

import sys
import unittest

from django.test import LiveServerTestCase, tag
from django.utils.module_loading import import_string
from django.utils.six import with_metaclass
from django.utils.text import capfirst


class SeleniumTestCaseBase(type(LiveServerTestCase)):
    browsers = []
    browser = None

    def __new__(cls, name, bases, attrs):
        """
        Dynamically create new classes and add them to the test module
        when multiple browsers specs are provided (e.g. --selenium=firefox, chrome).
        """
        test_class = super(SeleniumTestCaseBase, cls).__new__(cls, name, bases, attrs)
        # If the test class is nor browser specific, neither a test base modify it.
        if (not test_class.browser and
                any(name.startswith('test') and callable(value) for name, value in attrs.items())):
            first_browser = test_class.browsers[0]
            test_class.browser = first_browser
            module = sys.modules[test_class.__module__]
            for browser in test_class.browsers[1:]:
                browser_test_class = cls.__new__(
                    cls,
                    str("%s%s" % (capfirst(browser), name)),
                    (test_class,),
                    {'browser': browser, '__module__': test_class.__module__}
                )
                setattr(module, browser_test_class.__name__, browser_test_class)
        return test_class

    def create_webdriver(self):
        return import_string('selenium.webdriver.%s.webdriver.WebDriver' % self.browser)()


@tag('selenium')
class SeleniumTestCase(with_metaclass(SeleniumTestCaseBase, LiveServerTestCase)):

    @classmethod
    def setUpClass(cls):
        cls.selenium = cls.create_webdriver()
        cls.selenium.implicitly_wait(10)
        super(SeleniumTestCase, cls).setUpClass()

    @classmethod
    def _tearDownClassInternal(cls):
        # quit() the WebDriver before attempting to terminate and join the
        # single-threaded LiveServerThread to avoid a dead lock in case the
        # browser kept a connection alive.
        if hasattr(cls, 'selenium'):
            cls.selenium.quit()
        super(SeleniumTestCase, cls)._tearDownClassInternal()
