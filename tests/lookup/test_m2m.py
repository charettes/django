from django.test import TestCase

from .models import Player, Season


class M2MLookupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        season = Season.objects.create(year=2017)
        first_game = season.games.create(home='Montréal', away='Boston')
        second_game = season.games.create(home='Philadelphia', away='Montréal')
        cls.first_player = Player.objects.create(name='First')
        cls.second_player = Player.objects.create(name='Second')
        cls.second_player.games.set([first_game, second_game])

    def test_basic_exists(self):
        self.assertSequenceEqual(Player.objects.filter(games__exists__home='Montréal'))
        self.assertSequenceEqual(Player.objects.filter(games__exists=True), [self.second_player])
        self.assertSequenceEqual(Player.objects.filter(games__exists=False), [self.first_player])
