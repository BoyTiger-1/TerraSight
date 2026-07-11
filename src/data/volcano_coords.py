# real coordinates for USGS-monitored volcanoes, from the Smithsonian Global
# Volcanism Program catalog. the HANS alert feed gives live alert levels but
# no coordinates, so we join on (normalized) volcano name.

VOLCANO_COORDS = {
    # Alaska Volcano Observatory
    "akutan": (54.13, -165.99), "augustine": (59.36, -153.43),
    "bogoslof": (53.93, -168.03), "cleveland": (52.82, -169.94),
    "great sitkin": (52.08, -176.13), "iliamna": (60.03, -153.09),
    "kanaga": (51.92, -177.16), "katmai": (58.28, -154.96),
    "korovin": (52.38, -174.15), "makushin": (53.89, -166.92),
    "okmok": (53.43, -168.13), "pavlof": (55.42, -161.89),
    "redoubt": (60.49, -152.74), "semisopochnoi": (51.93, 179.58),
    "shishaldin": (54.76, -163.97), "spurr": (61.30, -152.25),
    "veniaminof": (56.17, -159.38), "westdahl": (54.52, -164.65),
    "gareloi": (51.79, -178.79), "tanaga": (51.88, -178.15),
    "trident": (58.24, -155.10), "ugashik-peulik": (57.75, -156.37),
    "aniakchak": (56.91, -158.21), "dutton": (55.19, -162.27),
    "edgecumbe": (57.05, -135.75), "wrangell": (62.00, -144.02),
    "kasatochi": (52.18, -175.51), "little sitkin": (51.95, 178.54),
    "atka volcanic complex": (52.38, -174.15), "davidof": (51.97, 178.33),
    "fourpeaked": (58.77, -153.67), "griggs": (58.35, -155.10),
    "isanotski": (54.77, -163.73), "kagamil": (52.97, -169.72),
    "kiska": (52.10, 177.60), "kupreanof": (56.01, -159.80),
    "martin": (58.17, -155.36), "mageik": (58.19, -155.25),
    "novarupta": (58.27, -155.16), "snowy mountain": (58.34, -154.68),
    "ukinrek maars": (57.83, -156.51),
    # Cascades Volcano Observatory
    "st. helens": (46.20, -122.18), "mount st. helens": (46.20, -122.18),
    "rainier": (46.85, -121.76), "hood": (45.37, -121.70),
    "adams": (46.21, -121.49), "baker": (48.78, -121.81),
    "glacier peak": (48.11, -121.11), "jefferson": (44.67, -121.80),
    "three sisters": (44.10, -121.77), "newberry": (43.72, -121.23),
    "crater lake": (42.94, -122.11), "shasta": (41.41, -122.19),
    "lassen volcanic center": (40.49, -121.51), "lassen": (40.49, -121.51),
    "medicine lake": (41.58, -121.57),
    # California Volcano Observatory
    "long valley volcanic region": (37.70, -118.87), "long valley": (37.70, -118.87),
    "mono-inyo craters": (37.88, -119.00), "clear lake volcanic field": (38.97, -122.77),
    "coso volcanic field": (36.03, -117.82), "salton buttes": (33.20, -115.62),
    "ubehebe craters": (37.02, -117.45),
    # Yellowstone Volcano Observatory region
    "yellowstone": (44.43, -110.67), "valles caldera": (35.87, -106.57),
    "san francisco volcanic field": (35.37, -111.50),
    # Hawaiian Volcano Observatory
    "kilauea": (19.42, -155.29), "mauna loa": (19.48, -155.61),
    "hualalai": (19.69, -155.87), "haleakala": (20.71, -156.25),
    "mauna kea": (19.82, -155.47), "kamaehuakanaloa": (18.92, -155.27),
    "loihi": (18.92, -155.27),
    # Northern Mariana Islands
    "anatahan": (16.35, 145.67), "pagan": (18.13, 145.80),
    "agrigan": (18.77, 145.67), "alamagan": (17.60, 145.83),
    "sarigan": (16.71, 145.78), "farallon de pajaros": (20.54, 144.90),
}


def lookup(name):
    """coordinates for a volcano name from the HANS feed, tolerant of prefixes"""
    if not name:
        return None
    key = name.lower().strip()
    for prefix in ["mount ", "mt. ", "mt "]:
        if key.startswith(prefix):
            key = key[len(prefix):]
    if key in VOLCANO_COORDS:
        return VOLCANO_COORDS[key]
    # fall back to a substring match, "Spurr" vs "Mount Spurr" etc.
    for k, v in VOLCANO_COORDS.items():
        if k in key or key in k:
            return v
    return None
