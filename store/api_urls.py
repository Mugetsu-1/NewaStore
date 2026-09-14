from django.urls import path
from store import api

urlpatterns = [
    path('search/', api.search, name='api_search'),
    path('browse/', api.browse, name='api_browse'),
    path('games/<slug:slug>/', api.game_detail, name='api_game_detail'),
    path('genres/', api.genres, name='api_genres'),
    path('contact/', api.contact, name='api_contact'),
    path('health/', api.health, name='api_health'),
]