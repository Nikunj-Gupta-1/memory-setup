from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

try:
    from scipy.spatial import cKDTree  # type: ignore
except Exception:  # pragma: no cover
    cKDTree = None

# Built-in reference dataset: (lat, lon, city, state_or_region, country)
BUILTIN_CITIES: List[Tuple[float, float, str, str, str]] = [
    # India - North & Delhi NCR
    (28.6139, 77.2090, "New Delhi", "Delhi", "India"),
    (28.7041, 77.1025, "Delhi", "Delhi", "India"),
    (28.4595, 77.0266, "Gurugram", "Haryana", "India"),
    (28.5355, 77.3910, "Noida", "Uttar Pradesh", "India"),
    (28.6692, 77.4538, "Ghaziabad", "Uttar Pradesh", "India"),
    (28.4089, 77.3178, "Faridabad", "Haryana", "India"),
    (31.1048, 77.1734, "Shimla", "Himachal Pradesh", "India"),
    (32.2432, 77.1892, "Manali", "Himachal Pradesh", "India"),
    (30.3165, 78.0322, "Dehradun", "Uttarakhand", "India"),
    (30.0869, 78.2676, "Rishikesh", "Uttarakhand", "India"),
    (29.9457, 78.1642, "Haridwar", "Uttarakhand", "India"),
    (29.5300, 79.1200, "Jim Corbett", "Uttarakhand", "India"),
    (30.7333, 76.7794, "Chandigarh", "Punjab", "India"),
    (31.6340, 74.8723, "Amritsar", "Punjab", "India"),
    (27.1767, 78.0081, "Agra", "Uttar Pradesh", "India"),
    (26.8467, 80.9462, "Lucknow", "Uttar Pradesh", "India"),
    (25.3176, 82.9739, "Varanasi", "Uttar Pradesh", "India"),
    (26.9124, 75.7873, "Jaipur", "Rajasthan", "India"),
    (26.2389, 73.0243, "Jodhpur", "Rajasthan", "India"),
    (24.5854, 73.7125, "Udaipur", "Rajasthan", "India"),
    # India - West & Mumbai
    (19.0760, 72.8777, "Mumbai", "Maharashtra", "India"),
    (19.0695, 72.8373, "Bandra, Mumbai", "Maharashtra", "India"),
    (19.1400, 72.8600, "Andheri/Goregaon, Mumbai", "Maharashtra", "India"),
    (19.2183, 72.9781, "Thane", "Maharashtra", "India"),
    (18.5204, 73.8567, "Pune", "Maharashtra", "India"),
    (19.9975, 73.7898, "Nashik", "Maharashtra", "India"),
    (18.7546, 73.4062, "Lonavala", "Maharashtra", "India"),
    (15.2993, 74.1240, "Goa", "Goa", "India"),
    (15.4909, 73.8278, "Panaji", "Goa", "India"),
    (15.2736, 73.9582, "Margao", "Goa", "India"),
    (15.5494, 73.7535, "Calangute, Goa", "Goa", "India"),
    (23.0225, 72.5714, "Ahmedabad", "Gujarat", "India"),
    # India - South & East
    (12.9716, 77.5946, "Bengaluru", "Karnataka", "India"),
    (12.8937, 77.5802, "JP Nagar, Bengaluru", "Karnataka", "India"),
    (13.0827, 80.2707, "Chennai", "Tamil Nadu", "India"),
    (17.3850, 78.4867, "Hyderabad", "Telangana", "India"),
    (9.9312, 76.2673, "Kochi", "Kerala", "India"),
    (22.5726, 88.3639, "Kolkata", "West Bengal", "India"),
    # UAE & Middle East
    (25.2048, 55.2708, "Dubai", "Dubai", "United Arab Emirates"),
    (25.1300, 55.4200, "Dubai", "Dubai", "United Arab Emirates"),
    (24.4539, 54.3773, "Abu Dhabi", "Abu Dhabi", "United Arab Emirates"),
    (25.3463, 55.4209, "Sharjah", "Sharjah", "United Arab Emirates"),
    (25.2854, 51.5310, "Doha", "Ad Dawhah", "Qatar"),
    (24.7136, 46.6753, "Riyadh", "Riyadh", "Saudi Arabia"),
    (23.5880, 58.3829, "Muscat", "Muscat", "Oman"),
    # Japan & East Asia
    (35.6762, 139.6503, "Tokyo", "Tokyo", "Japan"),
    (35.7000, 139.7700, "Ueno/Asakusa, Tokyo", "Tokyo", "Japan"),
    (35.0116, 135.7681, "Kyoto", "Kyoto", "Japan"),
    (34.6937, 135.5023, "Osaka", "Osaka", "Japan"),
    (34.6851, 135.8048, "Nara", "Nara", "Japan"),
    (35.3606, 138.7274, "Mount Fuji", "Shizuoka", "Japan"),
    (37.5665, 126.9780, "Seoul", "Seoul", "South Korea"),
    (22.3193, 114.1694, "Hong Kong", "Hong Kong", "China"),
    (25.0330, 121.5654, "Taipei", "Taipei", "Taiwan"),
    (31.2304, 121.4737, "Shanghai", "Shanghai", "China"),
    (39.9042, 116.4074, "Beijing", "Beijing", "China"),
    # Europe
    (48.8566, 2.3522, "Paris", "Île-de-France", "France"),
    (43.7102, 7.2620, "Nice", "Provence-Alpes-Côte d'Azur", "France"),
    (51.5074, -0.1278, "London", "England", "United Kingdom"),
    (55.9533, -3.1883, "Edinburgh", "Scotland", "United Kingdom"),
    (41.9028, 12.4964, "Rome", "Lazio", "Italy"),
    (45.4408, 12.3155, "Venice", "Veneto", "Italy"),
    (43.7696, 11.2558, "Florence", "Tuscany", "Italy"),
    (45.4642, 9.1900, "Milan", "Lombardy", "Italy"),
    (46.9480, 7.4474, "Bern", "Bern", "Switzerland"),
    (47.3769, 8.5417, "Zurich", "Zurich", "Switzerland"),
    (46.2044, 6.1432, "Geneva", "Geneva", "Switzerland"),
    (46.6863, 7.8632, "Interlaken", "Bern", "Switzerland"),
    (47.0502, 8.3093, "Lucerne", "Lucerne", "Switzerland"),
    (52.5200, 13.4050, "Berlin", "Berlin", "Germany"),
    (48.1351, 11.5820, "Munich", "Bavaria", "Germany"),
    (50.1109, 8.6821, "Frankfurt", "Hesse", "Germany"),
    (52.3676, 4.9041, "Amsterdam", "North Holland", "Netherlands"),
    (50.8503, 4.3517, "Brussels", "Brussels", "Belgium"),
    (48.2082, 16.3738, "Vienna", "Vienna", "Austria"),
    (50.0755, 14.4378, "Prague", "Prague", "Czechia"),
    (40.4168, -3.7038, "Madrid", "Madrid", "Spain"),
    (41.3851, 2.1734, "Barcelona", "Catalonia", "Spain"),
    (38.7223, -9.1393, "Lisbon", "Lisbon", "Portugal"),
    (37.9838, 23.7275, "Athens", "Attica", "Greece"),
    (41.0082, 28.9784, "Istanbul", "Istanbul", "Turkey"),
    (59.3293, 18.0686, "Stockholm", "Stockholm", "Sweden"),
    (55.6761, 12.5683, "Copenhagen", "Capital Region", "Denmark"),
    (59.9139, 10.7522, "Oslo", "Oslo", "Norway"),
    (64.1466, -21.9426, "Reykjavik", "Capital Region", "Iceland"),
    (53.3498, -6.2603, "Dublin", "Leinster", "Ireland"),
    # Southeast Asia
    (1.3521, 103.8198, "Singapore", "Central", "Singapore"),
    (13.7563, 100.5018, "Bangkok", "Bangkok", "Thailand"),
    (7.8804, 98.3923, "Phuket", "Phuket", "Thailand"),
    (3.1390, 101.6869, "Kuala Lumpur", "Federal Territory", "Malaysia"),
    (-8.4095, 115.1889, "Bali", "Bali", "Indonesia"),
    (21.0278, 105.8342, "Hanoi", "Hanoi", "Vietnam"),
    (10.8231, 106.6297, "Ho Chi Minh City", "Ho Chi Minh", "Vietnam"),
    (14.5995, 120.9842, "Manila", "Metro Manila", "Philippines"),
    # North America
    (40.7128, -74.0060, "New York", "New York", "United States"),
    (34.0522, -118.2437, "Los Angeles", "California", "United States"),
    (37.7749, -122.4194, "San Francisco", "California", "United States"),
    (47.6062, -122.3321, "Seattle", "Washington", "United States"),
    (36.1699, -115.1398, "Las Vegas", "Nevada", "United States"),
    (25.7617, -80.1918, "Miami", "Florida", "United States"),
    (28.5383, -81.3792, "Orlando", "Florida", "United States"),
    (41.8781, -87.6298, "Chicago", "Illinois", "United States"),
    (42.3601, -71.0589, "Boston", "Massachusetts", "United States"),
    (38.9072, -77.0369, "Washington", "District of Columbia", "United States"),
    (30.2672, -97.7431, "Austin", "Texas", "United States"),
    (39.7392, -104.9903, "Denver", "Colorado", "United States"),
    (21.3069, -157.8583, "Honolulu", "Hawaii", "United States"),
    (43.6532, -79.3832, "Toronto", "Ontario", "Canada"),
    (49.2827, -123.1207, "Vancouver", "British Columbia", "Canada"),
    (45.5017, -73.5673, "Montreal", "Quebec", "Canada"),
    (19.4326, -99.1332, "Mexico City", "CDMX", "Mexico"),
    # Oceania, Africa & South America
    (-33.8688, 151.2093, "Sydney", "New South Wales", "Australia"),
    (-37.8136, 144.9631, "Melbourne", "Victoria", "Australia"),
    (-36.8509, 174.7645, "Auckland", "Auckland", "New Zealand"),
    (-33.9249, 18.4241, "Cape Town", "Western Cape", "South Africa"),
    (30.0444, 31.2357, "Cairo", "Cairo", "Egypt"),
    (-23.5505, -46.6333, "São Paulo", "São Paulo", "Brazil"),
    (-22.9068, -43.1729, "Rio de Janeiro", "Rio de Janeiro", "Brazil"),
    (-34.6037, -58.3816, "Buenos Aires", "Buenos Aires", "Argentina"),
]

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return EARTH_RADIUS_KM * c


def _to_unit_ecef(lat: float, lon: float) -> Tuple[float, float, float]:
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    cos_lat = math.cos(lat_r)
    return (
        cos_lat * math.cos(lon_r),
        cos_lat * math.sin(lon_r),
        math.sin(lat_r),
    )


class OfflineGeocoder:
    """3D unit-sphere cKDTree reverse geocoder with strict 150 km distance cutoff."""

    def __init__(
        self,
        cities_db_path: Optional[Path] = None,
        max_distance_km: float = 150.0,
    ) -> None:
        self.max_distance_km = max_distance_km
        self.cities: List[Tuple[float, float, str, str, str]] = list(BUILTIN_CITIES)

        if cities_db_path and Path(cities_db_path).exists():
            self._load_sqlite_cities(Path(cities_db_path))

        coords = np.array([_to_unit_ecef(c[0], c[1]) for c in self.cities], dtype=np.float64)
        self._tree = cKDTree(coords) if cKDTree is not None and len(coords) > 0 else None

    def _load_sqlite_cities(self, db_path: Path) -> None:
        try:
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT latitude, longitude, name, admin1, country FROM cities"
            ).fetchall()
            conn.close()
            if rows:
                self.cities = [(float(r[0]), float(r[1]), str(r[2]), str(r[3] or ""), str(r[4])) for r in rows]
        except Exception:
            pass

    def lookup(
        self,
        lat: Optional[float],
        lon: Optional[float],
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        if lat is None or lon is None:
            return None, None, None
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            return None, None, None

        if math.isnan(lat_f) or math.isnan(lon_f) or not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
            return None, None, None

        if self._tree is not None:
            vec = _to_unit_ecef(lat_f, lon_f)
            _, idx = self._tree.query(vec)
            clat, clon, city, state, country = self.cities[int(idx)]
            dist = haversine_km(lat_f, lon_f, clat, clon)
            if dist <= self.max_distance_km:
                return city, state, country
            return None, None, None

        best_dist = float("inf")
        best_match: Tuple[Optional[str], Optional[str], Optional[str]] = (None, None, None)
        for clat, clon, city, state, country in self.cities:
            dist = haversine_km(lat_f, lon_f, clat, clon)
            if dist < best_dist:
                best_dist = dist
                best_match = (city, state, country)
        if best_dist <= self.max_distance_km:
            return best_match
        return None, None, None


_DEFAULT_GEOCODER: Optional[OfflineGeocoder] = None


def reverse_geocode(
    lat: Optional[float],
    lon: Optional[float],
    max_distance_km: float = 150.0,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    global _DEFAULT_GEOCODER
    if _DEFAULT_GEOCODER is None or _DEFAULT_GEOCODER.max_distance_km != max_distance_km:
        default_sqlite = Path(__file__).resolve().parent / "assets" / "cities15000.sqlite"
        _DEFAULT_GEOCODER = OfflineGeocoder(
            cities_db_path=default_sqlite if default_sqlite.exists() else None,
            max_distance_km=max_distance_km,
        )
    return _DEFAULT_GEOCODER.lookup(lat, lon)
