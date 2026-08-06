"""Deterministic class-selection pathway: Sector → Business type → Activities.

Jonathan's reflection (15 Jul), and it corrects a real mistake:

  * The LinkedIn industry list (and SIC labels generally) can't discriminate on
    the thing that decides the class. Gymshark's registered activity is
    "non-specialised retail"; Deliveroo's is "holding company". The LABEL tells
    you almost nothing. A "beauty brand" needs class 3, 35, 41 or 44 depending
    entirely on what it actually DOES under the brand.
  * So the pathway must be deterministic, and never expose SIC to the user.

DIVISION OF LABOUR (this is the whole design)

    Temmy PROPOSES   — real filings say what businesses like this actually
                       register: classes AND terms, banded Always/Often
                       (sic_engine + the empirical seed). Source of truth.
    Activities DISPOSE — the client's own answers filter that set down to what
                       is genuinely theirs. Nothing is invented; a class only
                       survives if the client affirmed the activity behind it.

That filter is also the SkyKick/bad-faith guard: we don't hand anyone a class
they haven't claimed to use. `plan_to_expand` widens nothing on its own — it
flags the recommendation for an intention-to-use conversation.

    Sector → Business type → (precise SIC slice) → Temmy empirical classes+terms
           → filtered by Activities → banded output
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. User-facing sectors → business types → the precise SIC slice to ask Temmy
#    about. SIC is BACK-END ONLY — never shown to the client.
# ---------------------------------------------------------------------------

SECTORS: dict[str, dict[str, list[str]]] = {
    "Beauty, Hair & Personal Care": {
        "Hairdresser or barber": ["96020"],
        "Beauty salon": ["96020"],
        "Nail salon": ["96020"],
        "Aesthetics or cosmetic clinic": ["86220"],
        "Spa": ["96040"],
        "Tattoo or piercing studio": ["96090"],
        "Skincare brand": ["20420"],
        "Cosmetics or make-up brand": ["20420"],
        "Haircare brand": ["20420"],
        "Fragrance or perfume brand": ["20420"],
        "Soap, bath or candle brand": ["20420"],
        "Men's grooming brand": ["20420"],
        "Cosmetics retailer": ["47750"],
        "Beauty trainer or academy": ["85590"],
        "Mobile or freelance beautician": ["96020"],
    },
    "Health, Medical & Wellbeing": {
        "Dental practice": ["86230"],
        "GP or medical practice": ["86210"],
        "Private clinic or hospital": ["86101", "86102"],
        "Physiotherapist": ["86900"],
        "Chiropractor or osteopath": ["86900"],
        "Therapist or counsellor": ["86900"],
        "Optician or optometrist": ["86900"],
        "Nutritionist or dietitian": ["86900"],
        "Supplement or vitamin brand": ["10890"],
        "Medical device or equipment brand": ["32500"],
        "Pharmacy": ["47730"],
        "Care home or home care": ["87100"],
        "Wellness or health coach": ["96040"],
        "Alternative or complementary therapy": ["86900"],
        "Health app or digital health": ["62012"],
    },
    "Food, Drink & Hospitality": {
        "Restaurant": ["56101"],
        "Café or coffee shop": ["56102"],
        "Takeaway": ["56103"],
        "Food truck or mobile food": ["56103"],
        "Pub or bar": ["56302"],
        "Nightclub": ["56302"],
        "Catering company": ["56210"],
        "Bakery": ["10710"],
        "Food product brand": ["10890"],
        "Confectionery or snack brand": ["10821", "10822", "10890"],
        "Soft drinks or juice brand": ["11070"],
        "Coffee or tea brand": ["10832", "10831"],
        "Brewery": ["11050"],
        "Cider maker": ["11030"],
        "Distillery or spirits brand": ["11010"],
        "Winery or wine brand": ["11020"],
        "Low or no alcohol drinks brand": ["11070"],
        "Meal kit or food delivery": ["56103"],
        "Hotel restaurant or venue": ["56101"],
    },
    "Clothing, Fashion & Accessories": {
        "Clothing brand": ["14190"],
        "Streetwear or fashion label": ["14190"],
        "Sportswear or activewear brand": ["14190"],
        "Childrenswear brand": ["14190"],
        "Underwear or lingerie brand": ["14142", "14141"],
        "Swimwear brand": ["14190"],
        "Workwear or uniform brand": ["14120"],
        "Clothing retailer": ["47710"],
        "Online fashion retailer": ["47910"],
        "Footwear brand": ["15200"],
        "Jewellery brand": ["32120"],
        "Watch brand": ["32130"],
        "Bags or leather goods brand": ["15120"],
        "Eyewear or sunglasses brand": ["32500"],
        "Hats, scarves or accessories": ["14190"],
        "Fashion designer": ["74100"],
        "Textile or fabric supplier": ["13300"],
    },
    "Technology, Software & AI": {
        "Software as a Service (SaaS)": ["62012"],
        "Mobile app": ["62012"],
        "AI or machine learning platform": ["62012"],
        "Fintech platform": ["62012"],
        "E-commerce platform": ["62012"],
        "Marketplace platform": ["63120"],
        "Cybersecurity": ["62020"],
        "Data or analytics platform": ["63110"],
        "Cloud or hosting services": ["63110"],
        "IT consultancy": ["62020"],
        "IT support or managed services": ["62090"],
        "Web design or development": ["62012"],
        "Game developer": ["62011"],
        "Hardware, device or IoT": ["26200"],
        "Telecoms or connectivity services": ["61900"],
        "Blockchain or crypto platform": ["62012"],
    },
    "Education & Training": {
        "Online course provider": ["85590"],
        "E-learning platform": ["85590"],
        "Tutor or tuition service": ["85590"],
        "Corporate training provider": ["85590"],
        "Vocational or technical training": ["85320"],
        "Language school": ["85590"],
        "Driving school": ["85530"],
        "Nursery or pre-school": ["85100"],
        "School": ["85200"],
        "University or college": ["85422", "85421"],
        "Educational resources or materials": ["85590"],
        "Coaching or mentoring": ["85590"],
    },
    "Creative, Media & Entertainment": {
        "Photographer": ["74201"],
        "Videographer or film production": ["59111"],
        "Animation or post-production": ["59120"],
        "Graphic designer": ["74100"],
        "Product or industrial designer": ["74100"],
        "Musician, band or artist": ["90010"],
        "Record label": ["59200"],
        "Podcast or audio producer": ["59200"],
        "Publisher (books or magazines)": ["58110"],
        "Content creator or influencer": ["90030"],
        "Creative or content agency": ["73110"],
        "Broadcaster or streaming service": ["60200"],
        "Theatre or performing arts": ["90010"],
        "Gaming or esports": ["93199"],
        "Artist or illustrator": ["90030"],
    },
    "Building, Property & Home Services": {
        "Builder or construction company": ["41201"],
        "Property developer": ["41100"],
        "Architect": ["71111"],
        "Surveyor": ["71129"],
        "Estate agent": ["68310"],
        "Letting or property management": ["68320"],
        "Interior design": ["74100"],
        "Electrician": ["43210"],
        "Plumber or heating engineer": ["43220"],
        "Roofer": ["43910"],
        "Joiner or carpenter": ["43320"],
        "Painter or decorator": ["43341"],
        "Landscaper or gardener": ["81300"],
        "Cleaning services": ["81210"],
        "Kitchen or bathroom fitter": ["43320"],
        "Home improvement products": ["47520"],
    },
    "Retail & Ecommerce": {
        "Online shop": ["47910"],
        "Marketplace seller": ["47910"],
        "Dropshipping business": ["47910"],
        "Subscription box": ["47910"],
        "High street shop": ["47190"],
        "Convenience store or supermarket": ["47110"],
        "Gift or homeware shop": ["47599"],
        "Product reseller or distributor": ["47990"],
        "Wholesaler": ["46900"],
        "Import or export business": ["46900"],
    },
    "Professional & Business Services": {
        "Management consultant": ["70229"],
        "Marketing or advertising agency": ["73110"],
        "Digital marketing or SEO agency": ["73110"],
        "PR or communications agency": ["70210"],
        "Accountant or bookkeeper": ["69202"],
        "Recruiter or staffing agency": ["78109"],
        "HR consultancy": ["70229"],
        "Business coach": ["70229"],
        "Virtual assistant or admin services": ["82110"],
        "Translation services": ["74300"],
        "Market research": ["73200"],
        "Print or design services": ["18129"],
        "Franchise or licensing business": ["70229"],
    },
    "Finance, Legal & Compliance": {
        "Financial adviser or IFA": ["66220"],
        "Mortgage broker": ["66220"],
        "Insurance broker": ["66220"],
        "Insurance provider": ["65120"],
        "Claims management company": ["66220"],
        "Accountancy or audit firm": ["69201"],
        "Solicitor or law firm": ["69102"],
        "Barrister or chambers": ["69101", "691"],
        "Patent or trade mark attorney": ["69102"],
        "Bank or lender": ["64191"],
        "Investment or wealth management": ["66300"],
        "Accountancy or tax software": ["62012"],
        "Compliance or regulatory consultancy": ["70229"],
        "Debt advice or collection": ["82911", "82912", "829"],
    },
    "Sport, Fitness & Recreation": {
        "Gym or fitness studio": ["93130"],
        "Personal trainer": ["85510"],
        "Yoga or pilates studio": ["93130"],
        "Sports club or team": ["93120"],
        "Sports coaching": ["85510"],
        "Fitness app or platform": ["62012"],
        "Sports equipment brand": ["32300"],
        "Activewear brand": ["14190"],
        "Supplement or sports nutrition brand": ["10890"],
        "Leisure centre or facility": ["93110"],
        "Outdoor or adventure activities": ["93290"],
        "Golf club or course": ["93110"],
    },
    "Children, Family & Care": {
        "Nursery or childcare": ["88910"],
        "Childminder": ["88910"],
        "Baby products brand": ["32409"],
        "Children's toys or games brand": ["32409", "32401"],
        "Children's activities or clubs": ["93290"],
        "Parenting services or support": ["88990"],
        "Family support services": ["88990"],
        "Care or support services": ["87900"],
    },
    "Automotive, Transport & Logistics": {
        "Car dealership": ["45111"],
        "Vehicle servicing or repair": ["45200"],
        "MOT or garage": ["45200"],
        "Car parts or accessories brand": ["45310"],
        "Vehicle manufacturer": ["29100"],
        "EV or charging business": ["27900"],
        "Car hire or leasing": ["77110"],
        "Delivery or courier": ["53202"],
        "Haulage or freight": ["49410"],
        "Taxi or private hire": ["49320"],
        "Logistics or warehousing": ["52103"],
        "Removals": ["49420"],
        "Cycling or e-bike brand": ["30920"],
    },
    "Manufacturing & Industrial": {
        "Engineering services": ["71121"],
        "Machinery manufacturer": ["28990"],
        "Industrial products manufacturer": ["25990"],
        "Metal fabrication": ["25620"],
        "Plastics manufacturer": ["22290"],
        "Packaging manufacturer": ["22220"],
        "Electronics manufacturer": ["26120"],
        "Chemical manufacturer": ["20590"],
        "Furniture manufacturer": ["31090"],
        "Contract or white-label manufacturer": ["25990"],
        "3D printing or prototyping": ["25990"],
        "Tools or hardware brand": ["25730"],
    },
    "Pets & Animals": {
        "Pet food or treats brand": ["10920"],
        "Pet accessories brand": ["47760"],
        "Pet grooming": ["96090"],
        "Dog walking or pet sitting": ["96090"],
        "Veterinary practice": ["75000"],
        "Dog training or behaviour": ["96090"],
        "Pet shop": ["47760"],
        "Equestrian products or services": ["47760"],
    },
    "Events, Leisure & Tourism": {
        "Events company or planner": ["82301"],
        "Wedding services": ["82301"],
        "Conference or exhibition organiser": ["82301"],
        "Venue or event space": ["82301"],
        "Travel agent or tour operator": ["79110"],
        "Hotel or B&B": ["55100"],
        "Holiday lets or serviced accommodation": ["55209", "55201", "55202"],
        "Campsite or glamping": ["55300"],
        "Attraction or experience": ["93290"],
        "Festival or live events": ["90020"],
        "Entertainment or party services": ["93290"],
    },
    "Environmental, Energy & Utilities": {
        "Renewable energy generation": ["35110"],
        "Solar or heat pump installer": ["43210"],
        "Energy supplier or broker": ["35140"],
        "Recycling or waste management": ["38320"],
        "Environmental consultancy": ["70229"],
        "Water or utilities services": ["36000"],
        "Sustainability or carbon services": ["70229"],
        "Green or eco product brand": ["20411", "20412"],
    },
    "Charity, Community & Public Sector": {
        "Charity": ["94990"],
        "Community interest company (CIC)": ["94990"],
        "Social enterprise": ["94990"],
        "Membership or trade association": ["94120"],
        "Campaign or advocacy organisation": ["94990"],
        "Religious organisation": ["94910"],
        "Housing association": ["68201"],
        "Public sector body": ["84110"],
    },
}

# ---------------------------------------------------------------------------
# 2. Activities — the discriminator. Each maps to the classes it can justify.
#    These are the ONLY classes an activity lets through; the empirical set is
#    filtered to the union of the selected activities' allowances.
# ---------------------------------------------------------------------------

GOODS_CLASSES = set(range(1, 35))       # 1–34 are goods
SERVICE_CLASSES = set(range(35, 46))    # 35–45 are services

ACTIVITIES: dict[str, dict] = {
    "sell_own_products": {
        "label": "We sell our own physical products",
        "allows": GOODS_CLASSES,
        "note": "Goods classes — the products you brand and sell.",
    },
    "provide_services": {
        "label": "We provide services",
        "allows": SERVICE_CLASSES,
        "note": "Service classes for what you do for customers.",
    },
    "retail_others_goods": {
        "label": "We sell online / retail other people's goods",
        "allows": {35},
        "note": "Retail and online-shop services sit in class 35.",
    },
    "software_platform": {
        "label": "We create software, an app or a platform",
        "allows": {9, 42},
        "note": "Downloadable software is class 9; SaaS and development are 42.",
    },
    "training_education": {
        "label": "We provide training or education",
        "allows": {41},
        "note": "Training, courses and education are class 41.",
    },
    "media_content": {
        "label": "We produce media or content",
        "allows": {41},
        "note": "Entertainment and content production are class 41.",
    },
    "medical_beauty_therapeutic": {
        "label": "We offer medical, beauty or therapeutic services",
        "allows": {44},
        "note": "Treatment services are class 44.",
    },
    "manufacture_for_others": {
        "label": "We manufacture for others (white label)",
        "allows": {40},
        "note": "Treatment/processing of materials for others is class 40.",
    },
    "license_franchise": {
        "label": "We license or franchise the brand",
        "allows": {35},
        "note": "Franchising and business assistance sit in class 35.",
    },
}

# A flag, NOT an allowance. Post-SkyKick, intention to use is what bites.
EXPANSION_FLAG = "plan_to_expand"
EXPANSION_LABEL = "We plan to expand into new areas within 5 years"

# Output band meanings layered on the empirical bands.
RECOMMENDATION = {
    'core': 'Recommended core protection',
    'optional': 'Optional add-on',
    'unclaimed': 'Not recommended unless genuinely intended',
}


# ---------------------------------------------------------------------------
# Profession / product search tags.
#
# Jonathan: "SOC-style professions only as search tags." Nobody thinks "Food,
# Drink & Hospitality → Distillery" — they think "I make gin". So the search
# has to match what people CALL THEMSELVES (profession) and what they MAKE
# (product), then land them on the right business type.
#
# Only needed where the business-type name doesn't already contain the likely
# search word ("Brewery" needs no alias for "brewery"; "Distillery or spirits
# brand" badly needs "gin").
# ---------------------------------------------------------------------------

ALIASES: dict[str, list[str]] = {
    # Beauty
    "Hairdresser or barber": ["hair stylist", "salon", "barbershop", "hairstylist"],
    "Beauty salon": ["beautician", "aesthetician", "lashes", "brows", "waxing"],
    "Nail salon": ["nail tech", "manicure", "pedicure", "nails"],
    "Aesthetics or cosmetic clinic": ["botox", "filler", "injectables", "medspa", "aesthetic nurse"],
    "Spa": ["massage", "sauna", "wellness spa"],
    "Tattoo or piercing studio": ["tattooist", "tattoo artist", "piercer"],
    "Skincare brand": ["moisturiser", "serum", "cream", "cosmeceutical"],
    "Cosmetics or make-up brand": ["makeup", "lipstick", "foundation", "beauty brand"],
    "Haircare brand": ["shampoo", "conditioner", "hair products"],
    "Fragrance or perfume brand": ["perfume", "aftershave", "cologne", "scent"],
    "Soap, bath or candle brand": ["candles", "bath bombs", "soap maker", "wax melts"],
    "Men's grooming brand": ["beard oil", "grooming"],
    # Health
    "Dental practice": ["dentist", "orthodontist", "hygienist"],
    "GP or medical practice": ["doctor", "gp", "surgery", "clinic"],
    "Physiotherapist": ["physio", "sports therapy", "rehab"],
    "Chiropractor or osteopath": ["chiro", "osteo", "back pain"],
    "Therapist or counsellor": ["counselling", "psychotherapy", "psychologist", "cbt", "hypnotherapy"],
    "Optician or optometrist": ["optician", "eye test", "glasses", "optometry"],
    "Nutritionist or dietitian": ["nutrition", "dietician", "diet"],
    "Supplement or vitamin brand": ["vitamins", "protein", "supplements", "nootropic", "gummies"],
    "Medical device or equipment brand": ["medtech", "medical device"],
    "Pharmacy": ["chemist", "pharmacist", "dispensary"],
    "Care home or home care": ["care home", "domiciliary", "carer", "nursing home"],
    "Wellness or health coach": ["life coach", "wellbeing", "wellness"],
    "Alternative or complementary therapy": ["acupuncture", "reiki", "homeopathy", "reflexology"],
    "Health app or digital health": ["healthtech", "health app"],
    # Food & drink
    "Restaurant": ["restaurateur", "bistro", "eatery", "diner"],
    "Café or coffee shop": ["coffee shop", "cafe", "barista", "tearoom"],
    "Takeaway": ["takeout", "fish and chips", "kebab", "chippy"],
    "Food truck or mobile food": ["food truck", "street food", "food van", "pop up"],
    "Pub or bar": ["publican", "landlord", "cocktail bar", "taproom", "wine bar"],
    "Nightclub": ["club", "venue"],
    "Catering company": ["caterer", "event catering", "private chef"],
    "Bakery": ["baker", "patisserie", "cakes", "bread", "cake maker"],
    "Food product brand": ["sauce", "snack", "condiment", "food brand", "ready meal"],
    "Confectionery or snack brand": ["chocolate", "sweets", "crisps", "chocolatier"],
    "Soft drinks or juice brand": ["juice", "smoothie", "kombucha", "energy drink", "soda",
                                   "water brand", "non-alcoholic", "no and low", "0%"],
    "Coffee or tea brand": ["coffee roaster", "tea brand", "roastery"],
    "Brewery": ["brewer", "beer", "craft beer", "ale", "lager", "microbrewery", "brewpub"],
    "Cider maker": ["cider", "perry", "cidery"],
    "Low or no alcohol drinks brand": ["no and low", "0%", "alcohol free", "non alcoholic",
                                       "nolo", "alcohol-free beer", "mocktail"],
    "Distillery or spirits brand": ["gin", "whisky", "whiskey", "vodka", "rum", "tequila",
                                    "distiller", "spirits", "liqueur"],
    "Winery or wine brand": ["wine", "vineyard", "winemaker", "prosecco", "champagne"],
    "Meal kit or food delivery": ["meal kit", "food delivery", "recipe box"],
    # Fashion
    "Clothing brand": ["apparel", "fashion brand", "t-shirts", "clothing line", "garments"],
    "Streetwear or fashion label": ["streetwear", "fashion label", "hoodies"],
    "Sportswear or activewear brand": ["activewear", "gymwear", "athleisure", "sportswear"],
    "Childrenswear brand": ["kidswear", "baby clothes", "children's clothing"],
    "Underwear or lingerie brand": ["lingerie", "underwear", "loungewear"],
    "Swimwear brand": ["swimwear", "bikini", "swimsuit"],
    "Workwear or uniform brand": ["workwear", "uniforms", "ppe clothing"],
    "Clothing retailer": ["boutique", "fashion shop"],
    "Online fashion retailer": ["online boutique", "fashion ecommerce"],
    "Footwear brand": ["shoes", "trainers", "sneakers", "boots", "footwear"],
    "Jewellery brand": ["jeweller", "jewelry", "rings", "necklaces", "silversmith"],
    "Watch brand": ["watches", "watchmaker", "horology"],
    "Bags or leather goods brand": ["handbags", "leather goods", "wallets", "rucksack"],
    "Eyewear or sunglasses brand": ["sunglasses", "eyewear", "spectacles"],
    "Hats, scarves or accessories": ["hats", "caps", "scarves", "accessories", "milliner"],
    "Fashion designer": ["designer", "couture", "dressmaker", "tailor"],
    "Textile or fabric supplier": ["fabric", "textiles", "mill"],
    # Tech
    "Software as a Service (SaaS)": ["saas", "software as a service", "software", "platform",
                                     "software company", "startup", "tech company", "cloud software"],
    "Mobile app": ["app developer", "ios", "android", "mobile app"],
    "AI or machine learning platform": ["ai", "artificial intelligence", "machine learning",
                                        "llm", "ml"],
    "Fintech platform": ["fintech", "payments", "banking app"],
    "E-commerce platform": ["ecommerce", "online store platform", "shopify"],
    "Marketplace platform": ["marketplace", "two sided platform"],
    "Cybersecurity": ["cyber", "security software", "infosec", "pen testing"],
    "Data or analytics platform": ["data", "analytics", "bi", "dashboard"],
    "Cloud or hosting services": ["cloud", "hosting", "servers", "devops"],
    "IT consultancy": ["it consultant", "technology consultant"],
    "IT support or managed services": ["it support", "msp", "helpdesk"],
    "Web design or development": ["web design", "web developer", "website", "wordpress",
                                  "developer", "coder", "programmer"],
    "Game developer": ["games", "game studio", "gaming", "video games"],
    "Hardware, device or IoT": ["hardware", "iot", "device", "electronics"],
    "Telecoms or connectivity services": ["telecoms", "broadband", "isp", "mobile network"],
    "Blockchain or crypto platform": ["crypto", "blockchain", "web3", "nft"],
    # Education
    "Online course provider": ["online course", "courses", "digital course"],
    "E-learning platform": ["elearning", "lms", "edtech"],
    "Tutor or tuition service": ["tutor", "tuition", "tutoring"],
    "Corporate training provider": ["corporate training", "l&d", "workshops"],
    "Vocational or technical training": ["apprenticeship", "vocational", "nvq"],
    "Language school": ["language", "tefl", "esl"],
    "Driving school": ["driving instructor", "driving lessons"],
    "Nursery or pre-school": ["nursery", "preschool", "early years"],
    "Coaching or mentoring": ["coach", "mentor", "business coach"],
    # Creative
    "Photographer": ["photography", "photo", "wedding photographer"],
    "Videographer or film production": ["video", "film", "videographer", "production company"],
    "Animation or post-production": ["animation", "vfx", "motion graphics", "editing"],
    "Graphic designer": ["graphic design", "branding", "logo design", "designer"],
    "Product or industrial designer": ["product design", "industrial design"],
    "Musician, band or artist": ["musician", "band", "artist", "singer", "dj", "producer"],
    "Record label": ["label", "music label"],
    "Podcast or audio producer": ["podcast", "audio"],
    "Publisher (books or magazines)": ["publisher", "publishing", "author", "magazine"],
    "Content creator or influencer": ["influencer", "youtuber", "creator", "blogger", "tiktoker"],
    "Creative or content agency": ["agency", "creative agency", "content agency"],
    "Broadcaster or streaming service": ["broadcast", "streaming", "tv", "radio"],
    "Theatre or performing arts": ["theatre", "dance", "performer", "actor"],
    "Gaming or esports": ["esports", "streamer", "twitch"],
    "Artist or illustrator": ["illustrator", "painter", "artist", "art"],
    # Property & trades
    "Builder or construction company": ["builder", "construction", "contractor", "groundworks"],
    "Property developer": ["developer", "property development"],
    "Architect": ["architecture", "architectural"],
    "Surveyor": ["surveying", "rics", "quantity surveyor"],
    "Estate agent": ["estate agency", "realtor", "property agent"],
    "Letting or property management": ["letting agent", "landlord", "property management"],
    "Interior design": ["interiors", "interior designer", "home staging"],
    "Electrician": ["sparky", "electrical", "niceic"],
    "Plumber or heating engineer": ["plumbing", "gas engineer", "boiler", "heating"],
    "Roofer": ["roofing"],
    "Joiner or carpenter": ["carpenter", "joinery", "woodwork"],
    "Painter or decorator": ["decorator", "painting"],
    "Landscaper or gardener": ["gardener", "landscaping", "grounds maintenance", "tree surgeon"],
    "Cleaning services": ["cleaner", "cleaning company", "domestic cleaning"],
    "Kitchen or bathroom fitter": ["kitchen fitter", "bathroom fitter"],
    # Retail
    "Online shop": ["ecommerce", "webshop", "online store", "d2c"],
    "Marketplace seller": ["amazon seller", "etsy", "ebay seller"],
    "Dropshipping business": ["dropship"],
    "Subscription box": ["subscription"],
    "High street shop": ["shop", "store", "retailer"],
    "Convenience store or supermarket": ["corner shop", "supermarket", "grocery"],
    "Gift or homeware shop": ["gifts", "homeware", "home decor"],
    "Wholesaler": ["wholesale", "distributor", "trade supplier"],
    "Import or export business": ["import", "export"],
    # Professional
    "Management consultant": ["consultant", "consultancy", "advisory"],
    "Marketing or advertising agency": ["marketing", "advertising", "ad agency"],
    "Digital marketing or SEO agency": ["seo", "digital marketing", "ppc", "social media agency"],
    "PR or communications agency": ["pr", "public relations", "comms"],
    "Accountant or bookkeeper": ["accountant", "bookkeeping", "payroll"],
    "Recruiter or staffing agency": ["recruitment", "headhunter", "staffing", "talent"],
    "HR consultancy": ["hr", "human resources", "people"],
    "Virtual assistant or admin services": ["va", "virtual assistant", "admin"],
    "Translation services": ["translator", "interpreting", "localisation"],
    "Print or design services": ["printing", "printer", "signage"],
    # Finance & legal
    "Financial adviser or IFA": ["ifa", "financial adviser", "wealth", "pensions"],
    "Mortgage broker": ["mortgage", "mortgages"],
    "Insurance broker": ["insurance broker"],
    "Claims management company": ["claims", "cmc", "compensation"],
    "Solicitor or law firm": ["solicitor", "lawyer", "law firm", "legal", "conveyancing"],
    "Barrister or chambers": ["barrister", "chambers"],
    "Patent or trade mark attorney": ["patent attorney", "trade mark attorney", "ip firm"],
    "Bank or lender": ["bank", "lending", "loans", "credit"],
    "Investment or wealth management": ["investment", "wealth management", "fund"],
    "Debt advice or collection": ["debt", "collections"],
    # Sport
    "Gym or fitness studio": ["gym", "fitness", "crossfit", "studio"],
    "Personal trainer": ["pt", "personal training", "trainer"],
    "Yoga or pilates studio": ["yoga", "pilates", "barre"],
    "Sports club or team": ["football club", "rugby club", "sports club"],
    "Sports coaching": ["coach", "coaching", "academy"],
    "Fitness app or platform": ["fitness app", "workout app"],
    "Sports equipment brand": ["sports equipment", "fitness equipment", "gym equipment"],
    "Supplement or sports nutrition brand": ["protein", "pre workout", "sports nutrition"],
    # Children
    "Nursery or childcare": ["nursery", "childcare", "creche"],
    "Childminder": ["childminder", "nanny"],
    "Baby products brand": ["baby", "pram", "nappies", "baby brand"],
    "Children's toys or games brand": ["toys", "games", "toy brand"],
    "Children's activities or clubs": ["kids club", "soft play", "children's activities"],
    # Automotive
    "Car dealership": ["dealership", "car sales", "used cars"],
    "Vehicle servicing or repair": ["garage", "mechanic", "car repair", "bodyshop"],
    "MOT or garage": ["mot"],
    "Car parts or accessories brand": ["car parts", "automotive parts", "tuning"],
    "Vehicle manufacturer": ["car manufacturer", "automotive"],
    "EV or charging business": ["ev", "electric vehicle", "charging"],
    "Car hire or leasing": ["car hire", "leasing", "rental"],
    "Delivery or courier": ["courier", "delivery", "last mile"],
    "Haulage or freight": ["haulage", "freight", "trucking", "logistics"],
    "Taxi or private hire": ["taxi", "private hire", "minicab", "chauffeur"],
    "Logistics or warehousing": ["warehouse", "3pl", "fulfilment"],
    "Removals": ["removals", "man and van"],
    "Cycling or e-bike brand": ["bike", "bicycle", "e-bike", "cycling"],
    # Manufacturing
    "Engineering services": ["engineer", "engineering"],
    "Machinery manufacturer": ["machinery", "machines"],
    "Metal fabrication": ["fabrication", "welding", "steel"],
    "Plastics manufacturer": ["plastics", "injection moulding"],
    "Packaging manufacturer": ["packaging", "boxes", "labels"],
    "Electronics manufacturer": ["electronics", "pcb"],
    "Chemical manufacturer": ["chemicals"],
    "Furniture manufacturer": ["furniture", "cabinet maker"],
    "Contract or white-label manufacturer": ["white label", "contract manufacturing", "oem"],
    "3D printing or prototyping": ["3d printing", "prototyping"],
    "Tools or hardware brand": ["tools", "hardware"],
    # Pets
    "Pet food or treats brand": ["dog food", "cat food", "pet food", "treats"],
    "Pet accessories brand": ["pet accessories", "dog beds", "collars", "leads"],
    "Pet grooming": ["dog groomer", "grooming"],
    "Dog walking or pet sitting": ["dog walker", "pet sitter"],
    "Veterinary practice": ["vet", "veterinary", "vets"],
    "Dog training or behaviour": ["dog trainer", "behaviourist"],
    "Pet shop": ["pet shop", "pet store"],
    "Equestrian products or services": ["equestrian", "horse", "saddlery"],
    # Events
    "Events company or planner": ["events", "event planner", "event management"],
    "Wedding services": ["wedding", "bridal", "wedding planner"],
    "Conference or exhibition organiser": ["conference", "exhibition", "trade show"],
    "Venue or event space": ["venue", "event space"],
    "Travel agent or tour operator": ["travel agent", "tour operator", "holidays", "travel"],
    "Hotel or B&B": ["hotel", "b&b", "guest house", "inn"],
    "Holiday lets or serviced accommodation": ["airbnb", "holiday let", "serviced apartment"],
    "Campsite or glamping": ["campsite", "glamping", "caravan park"],
    "Attraction or experience": ["attraction", "experience days"],
    "Festival or live events": ["festival", "live events", "promoter"],
    # Energy
    "Renewable energy generation": ["renewables", "solar farm", "wind"],
    "Solar or heat pump installer": ["solar", "heat pump", "solar panels"],
    "Energy supplier or broker": ["energy supplier", "energy broker", "utilities"],
    "Recycling or waste management": ["recycling", "waste", "skip hire"],
    "Environmental consultancy": ["environmental", "ecology"],
    "Sustainability or carbon services": ["sustainability", "carbon", "net zero", "esg"],
    "Green or eco product brand": ["eco", "sustainable products", "green brand"],
    # Charity
    "Charity": ["charity", "non profit", "nonprofit", "fundraising"],
    "Community interest company (CIC)": ["cic", "social enterprise"],
    "Membership or trade association": ["association", "membership body", "trade body"],
    "Religious organisation": ["church", "mosque", "temple", "faith"],
    "Housing association": ["housing association", "social housing"],
}


def sectors() -> list[str]:
    return list(SECTORS.keys())


def _norm(s: str) -> str:
    return ' '.join((s or '').lower().replace('&', ' and ').split())


def _word_in(needle: str, haystack: str) -> bool:
    """Whole-word-start containment ('gin' matches 'gin brand', not
    'enGINeering'). Used for RANKING, not exclusion — a raw substring hit still
    appears, just far down the list."""
    import re as _re
    return bool(_re.search(r'\b' + _re.escape(needle), haystack))


def search(query: str, *, limit: int = 25) -> dict:
    """ONE global search over sectors AND business types.

    The destination is always a business type. A sector hit is a signpost: the
    client picks it and we show its business types. A business type hit goes
    straight to classes.

    Matching is CONTAINS (name, search tags, or sector), ranked so the obvious
    answer is top and the long tail is still there to scroll — 'gin' puts
    Distillery first and Engineering far below, rather than hiding either.
    """
    q = _norm(query)
    if not q:
        return {'results': []}

    hits: list[tuple] = []

    # --- sectors -----------------------------------------------------------
    for sector, bts in SECTORS.items():
        sn = _norm(sector)
        score = None
        if sn == q:
            score = 0
        elif sn.startswith(q):
            score = 3
        elif _word_in(q, sn):
            score = 6
        elif q in sn:
            score = 9
        if score is not None:
            hits.append((score, 0, len(sn), {
                'type': 'sector', 'name': sector, 'sector': sector,
                'count': len(bts), 'matched_on': sector,
            }))

    # --- business types ----------------------------------------------------
    for sector, bts in SECTORS.items():
        for bt in bts:
            n = _norm(bt)
            aliases = ALIASES.get(bt, [])
            an = [_norm(a) for a in aliases]
            score = None
            matched = bt
            if n == q:
                score = 0
            elif q in an:
                score, matched = 1, next(a for a in aliases if _norm(a) == q)
            elif n.startswith(q):
                score = 2
            elif any(a.startswith(q) for a in an):
                score, matched = 3, next(a for a in aliases if _norm(a).startswith(q))
            elif _word_in(q, n):
                score = 4
            elif any(_word_in(q, a) for a in an):
                score, matched = 5, next(a for a in aliases if _word_in(q, _norm(a)))
            elif q in n:
                score = 8          # raw substring — kept, but ranked low
            elif any(q in a for a in an):
                score, matched = 9, next(a for a in aliases if q in _norm(a))
            elif _word_in(q, _norm(sector)):
                score, matched = 10, sector   # everything in a matching sector
            if score is not None:
                hits.append((score, 1, len(n), {
                    'type': 'business_type', 'name': bt, 'sector': sector,
                    'matched_on': matched,
                }))

    # sector signposts sort above business types at the same score
    hits.sort(key=lambda h: (h[0], h[1], h[2]))
    return {'results': [h[3] for h in hits[:limit]]}


# Back-compat alias for the earlier endpoint name.
def search_business_types(query: str, *, limit: int = 12) -> dict:
    r = search(query, limit=limit)
    return {'results': [{'business_type': x['name'], 'sector': x['sector'],
                         'matched_on': x['matched_on']}
                        for x in r['results'] if x['type'] == 'business_type']}


def business_types(sector: str) -> list[str]:
    return list(SECTORS.get(sector, {}).keys())


def sic_for(sector: str, business_type: str) -> list[str]:
    return SECTORS.get(sector, {}).get(business_type, [])


def allowed_classes(activity_keys) -> set[int]:
    """Union of the classes the selected activities can justify."""
    allowed: set[int] = set()
    for k in (activity_keys or []):
        a = ACTIVITIES.get(k)
        if a:
            allowed |= set(a['allows'])
    return allowed


def resolve(sector: str, business_type: str, activity_keys=None,
            plan_to_expand: bool = False) -> dict | None:
    """The deterministic pathway.

    Temmy proposes (empirical classes + terms for this business type's SIC
    slice); the activities dispose (filter to what the client actually does).
    Returns classes tagged with a recommendation as well as the empirical band.
    """
    from . import sic_engine

    sics = sic_for(sector, business_type)
    if not sics:
        return None

    mapping = sic_engine.map_sic_codes(sics)
    allowed = allowed_classes(activity_keys)

    out_classes = []
    for c in mapping.get('classes', []):
        n = int(c['nice_class'])
        claimed = (not allowed) or (n in allowed)
        if claimed:
            # Empirically strong AND claimed by an activity -> core.
            rec = 'core' if c.get('tier') in ('a', 'b') else 'optional'
        else:
            # Businesses like this do file it, but the client hasn't claimed
            # the activity behind it. Never auto-selected (SkyKick guard).
            rec = 'unclaimed'
        out_classes.append({**c, 'recommendation': rec,
                            'recommendation_label': RECOMMENDATION[rec],
                            'selected': rec == 'core'})

    order = {'core': 0, 'optional': 1, 'unclaimed': 2}
    out_classes.sort(key=lambda c: (order[c['recommendation']],
                                    -{'a': 3, 'b': 2, 'c': 1, 'd': 0}[c['tier']],
                                    c['nice_class']))

    warnings = []
    if plan_to_expand:
        warnings.append(
            'You told us you plan to expand. We have NOT added classes for that '
            '— a trade mark must reflect genuine intention to use. Filing for '
            'goods or services you do not intend to offer risks a bad-faith '
            'challenge (SkyKick). Let’s discuss your 5-year plan properly.')
    if not activity_keys:
        warnings.append(
            'No activities selected, so this shows everything businesses like '
            'yours register. Tell us what you actually do to narrow it.')

    return {
        'sector': sector,
        'business_type': business_type,
        'activities': list(activity_keys or []),
        'plan_to_expand': bool(plan_to_expand),
        'method': mapping.get('method'),
        'classes': out_classes,
        'warnings': warnings,
        'basket': _basket(out_classes, sector, business_type),
    }


def _basket(classes, sector, business_type):
    """Only 'core' classes go into the basket, with their empirical terms."""
    from .term_basket import TermBasket, ClassEntry, Term
    b = TermBasket(source_type='taxonomy',
                   source_ref=f'{sector} / {business_type}',
                   source_label=business_type)
    for c in classes:
        if c['recommendation'] != 'core':
            continue
        e = ClassEntry(nice_class=int(c['nice_class']),
                       heading=c.get('heading', ''),
                       class_label=c.get('class_label', ''),
                       source=f"{business_type} ({c.get('band')})")
        for t in (c.get('terms') or []):
            e.terms.append(Term(text=t['text'],
                                kept=t.get('band') in ('a', 'b')))
        b.entries.append(e)
    b.entries.sort(key=lambda e: e.nice_class)
    return b.to_dict()
