"""Short, human class names for the 45 Nice classes.

A client shown "Class 3" learns nothing. They need "Class 3 — Cosmetics &
cleaning preparations", with the official heading available underneath.

Two layers, both surfaced by the engine:
  * `class_label` (here)  — the short, friendly name for the UI.
  * `heading` (nice_classes.NICE_HEADINGS) — the official, full Nice class
    heading. Legally authoritative, but far too long to be a UI label; show it
    as the description / on expand.
"""
from __future__ import annotations

NICE_SHORT: dict[int, str] = {
    1: "Chemicals",
    2: "Paints & coatings",
    3: "Cosmetics & cleaning preparations",
    4: "Oils, lubricants & fuels",
    5: "Pharmaceuticals & health preparations",
    6: "Metals & metal products",
    7: "Machinery & machine tools",
    8: "Hand tools & cutlery",
    9: "Software, electronics & scientific devices",
    10: "Medical & surgical apparatus",
    11: "Lighting, heating & sanitary",
    12: "Vehicles",
    13: "Firearms & fireworks",
    14: "Jewellery & watches",
    15: "Musical instruments",
    16: "Paper, printed matter & stationery",
    17: "Rubber & plastics (semi-processed)",
    18: "Leather goods & luggage",
    19: "Building materials (non-metal)",
    20: "Furniture & fittings",
    21: "Household & kitchen utensils",
    22: "Ropes, nets, tents & tarpaulins",
    23: "Yarns & threads",
    24: "Textiles & household linen",
    25: "Clothing, footwear & headgear",
    26: "Lace, ribbons & haberdashery",
    27: "Carpets & floor coverings",
    28: "Games, toys & sporting goods",
    29: "Meat, fish, dairy & prepared foods",
    30: "Coffee, bakery & staple foods",
    31: "Fresh produce, plants & live animals",
    32: "Beers & soft drinks",
    33: "Alcoholic beverages",
    34: "Tobacco & smokers' articles",
    35: "Advertising, retail & business services",
    36: "Financial, insurance & property services",
    37: "Construction, installation & repair",
    38: "Telecommunications",
    39: "Transport, storage & travel",
    40: "Treatment of materials (manufacturing for others)",
    41: "Education, training & entertainment",
    42: "IT, software development & science",
    43: "Food & drink services, accommodation",
    44: "Medical, beauty & wellbeing services",
    45: "Legal, security & personal services",
}


def short(nice_class) -> str:
    try:
        return NICE_SHORT.get(int(nice_class), '')
    except (TypeError, ValueError):
        return ''
