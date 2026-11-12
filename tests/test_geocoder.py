from __future__ import annotations

from memory_setup.geocoding.offline_tree import OfflineGeocoder, reverse_geocode


def test_geocoder_known_world_cities() -> None:
    geo = OfflineGeocoder()

    # Dubai (~25.2048, 55.2708)
    city, admin, country = geo.lookup(25.20, 55.27)
    assert city == "Dubai"
    assert country == "United Arab Emirates"

    # London (~51.5072, -0.1276)
    city_ldn, _, country_ldn = geo.lookup(51.51, -0.13)
    assert city_ldn == "London"
    assert country_ldn == "United Kingdom"


def test_geocoder_strict_150km_ocean_cutoff() -> None:
    # Null Island (0.0, 0.0) in the Gulf of Guinea -> must NOT fall through to Lagos/Accra (>150 km away)
    city, admin, country = reverse_geocode(0.0, 0.0, max_distance_km=150.0)
    assert (city, admin, country) == (None, None, None)

    # Middle of the Pacific Ocean
    assert reverse_geocode(0.0, -160.0, max_distance_km=150.0) == (None, None, None)
