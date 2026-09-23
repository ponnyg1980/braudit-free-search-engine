"""The 45 class descriptions TMH writes to Class_Description_Snapshot.

Jonathan, 23 Sep 2026: "Official Classes & Terms Descriptions are: ..." --
these, verbatim. They are what sits beside the terms on every G S Scope class
row, whoever writes it (Class Builder, checkout, staff order, the Trademark /
sub-form sync). Read-only in every tool (ruling the same day: "Heading of
Classes and Terms is official and should not change").

Class 34 arrived truncated ("Tobacco and smokers'"); completed as
"Tobacco and smokers' articles." -- change it here if that is wrong.

THIS FILE EXISTS TWICE, deliberately identical:
    freesearch/class_headings.py            (Render: builder, /class-scope)
    temmy-access/audit_engine/class_headings.py   (droplet: scope sync)
Two deploys, no shared package. `python3 class_headings.py` prints a digest;
the two must print the same one.
"""
TMH_HEADINGS: dict[int, str] = {
    1: "Chemicals for industry, science, and photography.",
    2: "Paints, varnishes, and lacquers.",
    3: "Cleaning and bleaching preparations, cosmetics.",
    4: "Industrial oils, greases, and fuels.",
    5: "Pharmaceutical and veterinary preparations.",
    6: "Common metals and alloys.",
    7: "Machines and machine tools.",
    8: "Hand tools and implements.",
    9: "Nautical, scientific, electrical, and optical apparatus (including software and screens).",
    10: "Medical and surgical apparatus.",
    11: "Lighting, heating, cooking, and refrigerating equipment.",
    12: "Vehicles and locomotion apparatus (land, air, or water).",
    13: "Firearms, ammunition, and explosives.",
    14: "Precious metals, jewellery, and horological instruments.",
    15: "Musical instruments.",
    16: "Paper, cardboard, stationery, and office requisites.",
    17: "Rubber, gutta-percha, plastics, and packing materials.",
    18: "Leather, hides, trunks, travelling bags, and umbrellas.",
    19: "Non-metallic building materials.",
    20: "Furniture, mirrors, and non-metallic storage containers.",
    21: "Household or kitchen utensils and glassware.",
    22: "Ropes, string, tents, padding, and raw fibrous textile materials.",
    23: "Yarns and threads for textile use.",
    24: "Textiles and bed/table covers.",
    25: "Clothing, footwear, and headgear.",
    26: "Lace, embroidery, ribbons, buttons, and artificial flowers.",
    27: "Carpets, rugs, mats, and linoleum.",
    28: "Games, toys, and sporting articles.",
    29: "Meat, fish, poultry, preserved/cooked fruits, and dairy.",
    30: "Coffee, tea, cocoa, sugar, rice, flour, bread, and spices.",
    31: "Raw and unprocessed agricultural, aquacultural, horticultural, and forestry products.",
    32: "Beers, mineral waters, and non-alcoholic beverages.",
    33: "Alcoholic beverages (except beers).",
    34: "Tobacco and smokers' articles.",
    35: "Advertising, business management, and retail/wholesale services.",
    36: "Insurance, financial affairs, monetary affairs, and real estate.",
    37: "Construction, repair, and installation services.",
    38: "Telecommunications.",
    39: "Transport, packaging, storage of goods, and travel arrangement.",
    40: "Treatment and processing of materials.",
    41: "Education, training, entertainment, sporting, and cultural activities.",
    42: "Scientific, technological services, and computer/software development.",
    43: "Services for providing food, drink, and temporary accommodation.",
    44: "Medical services, veterinary services, and hygienic/beauty care.",
    45: "Legal services, security services, and personal/social services.",
}
assert sorted(TMH_HEADINGS) == list(range(1, 46))


def heading(n) -> str:
    try:
        return TMH_HEADINGS.get(int(n), '')
    except (TypeError, ValueError):
        return ''


if __name__ == '__main__':
    import hashlib, json
    print(hashlib.sha256(json.dumps(TMH_HEADINGS, sort_keys=True).encode()).hexdigest()[:16])
