# the 120 largest US cities with 2020 census populations, used to estimate how
# many people sit inside a hazard footprint. real census figures, not made up.
import math

# (name, state, lat, lon, population)
US_CITIES = [
    ("New York", "NY", 40.7128, -74.0060, 8804190),
    ("Los Angeles", "CA", 34.0522, -118.2437, 3898747),
    ("Chicago", "IL", 41.8781, -87.6298, 2746388),
    ("Houston", "TX", 29.7604, -95.3698, 2304580),
    ("Phoenix", "AZ", 33.4484, -112.0740, 1608139),
    ("Philadelphia", "PA", 39.9526, -75.1652, 1603797),
    ("San Antonio", "TX", 29.4241, -98.4936, 1434625),
    ("San Diego", "CA", 32.7157, -117.1611, 1386932),
    ("Dallas", "TX", 32.7767, -96.7970, 1304379),
    ("San Jose", "CA", 37.3382, -121.8863, 1013240),
    ("Austin", "TX", 30.2672, -97.7431, 961855),
    ("Jacksonville", "FL", 30.3322, -81.6557, 949611),
    ("Fort Worth", "TX", 32.7555, -97.3308, 918915),
    ("Columbus", "OH", 39.9612, -82.9988, 905748),
    ("Indianapolis", "IN", 39.7684, -86.1581, 887642),
    ("Charlotte", "NC", 35.2271, -80.8431, 874579),
    ("San Francisco", "CA", 37.7749, -122.4194, 873965),
    ("Seattle", "WA", 47.6062, -122.3321, 737015),
    ("Denver", "CO", 39.7392, -104.9903, 715522),
    ("Washington", "DC", 38.9072, -77.0369, 689545),
    ("Nashville", "TN", 36.1627, -86.7816, 689447),
    ("Oklahoma City", "OK", 35.4676, -97.5164, 681054),
    ("El Paso", "TX", 31.7619, -106.4850, 678815),
    ("Boston", "MA", 42.3601, -71.0589, 675647),
    ("Portland", "OR", 45.5152, -122.6784, 652503),
    ("Las Vegas", "NV", 36.1699, -115.1398, 641903),
    ("Detroit", "MI", 42.3314, -83.0458, 639111),
    ("Memphis", "TN", 35.1495, -90.0490, 633104),
    ("Louisville", "KY", 38.2527, -85.7585, 633045),
    ("Baltimore", "MD", 39.2904, -76.6122, 585708),
    ("Milwaukee", "WI", 43.0389, -87.9065, 577222),
    ("Albuquerque", "NM", 35.0844, -106.6504, 564559),
    ("Tucson", "AZ", 32.2226, -110.9747, 542629),
    ("Fresno", "CA", 36.7378, -119.7871, 542107),
    ("Sacramento", "CA", 38.5816, -121.4944, 524943),
    ("Kansas City", "MO", 39.0997, -94.5786, 508090),
    ("Mesa", "AZ", 33.4152, -111.8315, 504258),
    ("Atlanta", "GA", 33.7490, -84.3880, 498715),
    ("Omaha", "NE", 41.2565, -95.9345, 486051),
    ("Colorado Springs", "CO", 38.8339, -104.8214, 478961),
    ("Raleigh", "NC", 35.7796, -78.6382, 467665),
    ("Long Beach", "CA", 33.7701, -118.1937, 466742),
    ("Virginia Beach", "VA", 36.8529, -75.9780, 459470),
    ("Miami", "FL", 25.7617, -80.1918, 442241),
    ("Oakland", "CA", 37.8044, -122.2712, 440646),
    ("Minneapolis", "MN", 44.9778, -93.2650, 429954),
    ("Tulsa", "OK", 36.1540, -95.9928, 413066),
    ("Bakersfield", "CA", 35.3733, -119.0187, 403455),
    ("Wichita", "KS", 37.6872, -97.3301, 397532),
    ("Arlington", "TX", 32.7357, -97.1081, 394266),
    ("Aurora", "CO", 39.7294, -104.8319, 386261),
    ("Tampa", "FL", 27.9506, -82.4572, 384959),
    ("New Orleans", "LA", 29.9511, -90.0715, 383997),
    ("Cleveland", "OH", 41.4993, -81.6944, 372624),
    ("Honolulu", "HI", 21.3069, -157.8583, 350964),
    ("Anaheim", "CA", 33.8366, -117.9143, 346824),
    ("Lexington", "KY", 38.0406, -84.5037, 322570),
    ("Stockton", "CA", 37.9577, -121.2908, 320804),
    ("Corpus Christi", "TX", 27.8006, -97.3964, 317863),
    ("Henderson", "NV", 36.0395, -114.9817, 317610),
    ("Riverside", "CA", 33.9533, -117.3962, 314998),
    ("Newark", "NJ", 40.7357, -74.1724, 311549),
    ("Saint Paul", "MN", 44.9537, -93.0900, 311527),
    ("Santa Ana", "CA", 33.7455, -117.8677, 310227),
    ("Cincinnati", "OH", 39.1031, -84.5120, 309317),
    ("Irvine", "CA", 33.6846, -117.8265, 307670),
    ("Orlando", "FL", 28.5383, -81.3792, 307573),
    ("Pittsburgh", "PA", 40.4406, -79.9959, 302971),
    ("St. Louis", "MO", 38.6270, -90.1994, 301578),
    ("Greensboro", "NC", 36.0726, -79.7920, 299035),
    ("Jersey City", "NJ", 40.7178, -74.0431, 292449),
    ("Anchorage", "AK", 61.2181, -149.9003, 291247),
    ("Lincoln", "NE", 40.8136, -96.7026, 291082),
    ("Plano", "TX", 33.0198, -96.6989, 285494),
    ("Durham", "NC", 35.9940, -78.8986, 283506),
    ("Buffalo", "NY", 42.8864, -78.8784, 278349),
    ("Chandler", "AZ", 33.3062, -111.8413, 275987),
    ("Chula Vista", "CA", 32.6401, -117.0842, 275487),
    ("Toledo", "OH", 41.6528, -83.5379, 270871),
    ("Madison", "WI", 43.0731, -89.4012, 269840),
    ("Gilbert", "AZ", 33.3528, -111.7890, 267918),
    ("Reno", "NV", 39.5296, -119.8138, 264165),
    ("Fort Wayne", "IN", 41.0793, -85.1394, 263886),
    ("North Las Vegas", "NV", 36.1989, -115.1175, 262527),
    ("St. Petersburg", "FL", 27.7676, -82.6403, 258308),
    ("Lubbock", "TX", 33.5779, -101.8552, 257141),
    ("Irving", "TX", 32.8140, -96.9489, 256684),
    ("Laredo", "TX", 27.5306, -99.4803, 255205),
    ("Winston-Salem", "NC", 36.0999, -80.2442, 249545),
    ("Chesapeake", "VA", 36.7682, -76.2875, 249422),
    ("Glendale", "AZ", 33.5387, -112.1860, 248325),
    ("Garland", "TX", 32.9126, -96.6389, 246018),
    ("Scottsdale", "AZ", 33.4942, -111.9261, 241361),
    ("Norfolk", "VA", 36.8508, -76.2859, 238005),
    ("Boise", "ID", 43.6150, -116.2023, 235684),
    ("Fremont", "CA", 37.5485, -121.9886, 230504),
    ("Spokane", "WA", 47.6588, -117.4260, 228989),
    ("Santa Clarita", "CA", 34.3917, -118.5426, 228673),
    ("Baton Rouge", "LA", 30.4515, -91.1871, 227470),
    ("Richmond", "VA", 37.5407, -77.4360, 226610),
    ("Hialeah", "FL", 25.8576, -80.2781, 223109),
    ("San Bernardino", "CA", 34.1083, -117.2898, 222101),
    ("Tacoma", "WA", 47.2529, -122.4443, 219346),
    ("Modesto", "CA", 37.6391, -120.9969, 218464),
    ("Huntsville", "AL", 34.7304, -86.5861, 215006),
    ("Des Moines", "IA", 41.5868, -93.6250, 214133),
    ("Yonkers", "NY", 40.9312, -73.8988, 211569),
    ("Rochester", "NY", 43.1566, -77.6088, 211328),
    ("Moreno Valley", "CA", 33.9425, -117.2297, 208634),
    ("Fayetteville", "NC", 35.0527, -78.8784, 208501),
    ("Fontana", "CA", 34.0922, -117.4350, 208393),
    ("Worcester", "MA", 42.2626, -71.8023, 206518),
    ("Port St. Lucie", "FL", 27.2730, -80.3582, 204851),
    ("Little Rock", "AR", 34.7465, -92.2896, 202591),
    ("Augusta", "GA", 33.4735, -82.0105, 202081),
    ("Oxnard", "CA", 34.1975, -119.1771, 202063),
    ("Birmingham", "AL", 33.5186, -86.8104, 200733),
    ("Montgomery", "AL", 32.3792, -86.3077, 200603),
    ("Amarillo", "TX", 35.2220, -101.8313, 200393),
    ("Salt Lake City", "UT", 40.7608, -111.8910, 199723),
]


def nearby_cities(lat, lon, radius_km):
    """cities inside a radius, sorted closest first"""
    hits = []
    for name, state, clat, clon, pop in US_CITIES:
        # inline haversine to avoid an import cycle with services
        p1, p2 = math.radians(lat), math.radians(clat)
        dp = math.radians(clat - lat)
        dl = math.radians(clon - lon)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        dist = 2 * 6371.0 * math.asin(math.sqrt(a))
        if dist <= radius_km:
            hits.append({"name": name, "state": state, "lat": clat, "lon": clon,
                         "population": pop, "distance_km": round(dist, 1)})
    return sorted(hits, key=lambda c: c["distance_km"])


def population_exposure(lat, lon, radius_km):
    """rough headcount inside a radius: city populations decay with distance,
    plus a rural background density so empty map areas are not literally zero"""
    exposed = 0.0
    for c in nearby_cities(lat, lon, radius_km):
        # a city right at the center counts fully, one at the edge counts ~20%
        w = max(0.2, 1.0 - 0.8 * (c["distance_km"] / max(radius_km, 1)))
        exposed += c["population"] * w
    # CONUS averages ~36 people/km2 outside major cities, use a conservative 15
    rural = 15.0 * math.pi * radius_km ** 2
    return int(exposed + rural)
