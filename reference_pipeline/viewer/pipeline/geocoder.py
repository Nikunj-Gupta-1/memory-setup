import math
from typing import Tuple, Optional, Dict

# World Cities Reference (Lat, Lon, City, State/Region, Country)
# Covers major global destinations, travel hubs, and family trip destinations
CITIES_DB = [
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

    # India - South
    (12.9716, 77.5946, "Bengaluru", "Karnataka", "India"),
    (12.8937, 77.5802, "JP Nagar, Bengaluru", "Karnataka", "India"),
    (13.0827, 80.2707, "Chennai", "Tamil Nadu", "India"),
    (17.3850, 78.4867, "Hyderabad", "Telangana", "India"),
    (9.9312, 76.2673, "Kochi", "Kerala", "India"),

    # UAE & Middle East
    (25.2048, 55.2708, "Dubai", "Dubai", "United Arab Emirates"),
    (25.1300, 55.4200, "Dubai", "Dubai", "United Arab Emirates"),
    (24.4539, 54.3773, "Abu Dhabi", "Abu Dhabi", "United Arab Emirates"),
    (25.3463, 55.4209, "Sharjah", "Sharjah", "United Arab Emirates"),
    (25.2854, 51.5310, "Doha", "Ad Dawhah", "Qatar"),

    # Japan
    (35.6762, 139.6503, "Tokyo", "Tokyo", "Japan"),
    (35.7000, 139.7700, "Ueno/Asakusa, Tokyo", "Tokyo", "Japan"),
    (35.0116, 135.7681, "Kyoto", "Kyoto", "Japan"),
    (34.6937, 135.5023, "Osaka", "Osaka", "Japan"),
    (34.6851, 135.8048, "Nara", "Nara", "Japan"),
    (35.3606, 138.7274, "Mount Fuji", "Shizuoka", "Japan"),

    # Europe - France, UK, Italy, Switzerland, Germany, etc.
    (48.8566, 2.3522, "Paris", "Île-de-France", "France"),
    (43.7102, 7.2620, "Nice", "Provence-Alpes-Côte d'Azur", "France"),
    (51.5074, -0.1278, "London", "England", "United Kingdom"),
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

    # Southeast Asia
    (1.3521, 103.8198, "Singapore", "Central", "Singapore"),
    (13.7563, 100.5018, "Bangkok", "Bangkok", "Thailand"),
    (7.8804, 98.3923, "Phuket", "Phuket", "Thailand"),
    (3.1390, 101.6869, "Kuala Lumpur", "Federal Territory", "Malaysia"),
    (-8.4095, 115.1889, "Bali", "Bali", "Indonesia"),

    # United States & North America
    (40.7128, -74.0060, "New York", "New York", "United States"),
    (34.0522, -118.2437, "Los Angeles", "California", "United States"),
    (37.7749, -122.4194, "San Francisco", "California", "United States"),
    (47.6062, -122.3321, "Seattle", "Washington", "United States"),
    (36.1699, -115.1398, "Las Vegas", "Nevada", "United States"),
    (25.7617, -80.1918, "Miami", "Florida", "United States"),
    (28.5383, -81.3792, "Orlando", "Florida", "United States"),
    (43.6532, -79.3832, "Toronto", "Ontario", "Canada"),
]

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Radius of earth in km
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2.0) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

def reverse_geocode(lat: Optional[float], lon: Optional[float]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if lat is None or lon is None:
        return None, None, None
        
    best_dist = float('inf')
    best_match = (None, None, None)
    
    for clat, clon, city, state, country in CITIES_DB:
        dist = haversine_km(lat, lon, clat, clon)
        if dist < best_dist:
            best_dist = dist
            best_match = (city, state, country)
            
    # If nearest known city is within 150 km, return it
    if best_dist <= 150.0:
        return best_match
    # Otherwise return None
    return None, None, None
