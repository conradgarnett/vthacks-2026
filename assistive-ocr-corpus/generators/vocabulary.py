"""Everything the corpora are built out of, in one place.

Sample count and diversity are different things, and conflating them is the
easiest way to build a large dataset that measures nothing. Twelve drug names
rendered ten thousand times is ten thousand pictures of twelve drugs: a reader
that memorised the twelve scores perfectly, and a reader that generalises
scores the same. Raising `n` past the vocabulary size buys capture variation
-- curvature, scale, lighting, blur, noise -- and no new text at all.

So this file is the thing to grow. Adding fifty drug names here makes every
future run of the medicine corpus more informative; adding fifty to `n` does
not. Rough guide: `n` around 3-5x the relevant list is where capture variation
is well sampled and the text has stopped repeating too hard.

Sources and why they are safe to ship:

  Drug names are International Nonproprietary Names -- the generic names, not
  brands. They are facts about pharmacology, published by the WHO, and no
  brand mark appears anywhere in this file.

  Pharmacy names, patient names, products and brands are invented. Any
  resemblance to a real pharmacy or person is coincidence. This matters: a
  real prescription label is health information about an identifiable person,
  which is exactly why no public dataset of them exists and why these are
  synthetic.

  Allergen statements follow the wording conventions of US and EU labelling
  law (FALCPA and EU FIC 1169/2011), which is why "CONTAINS", "MAY CONTAIN"
  and "FREE FROM" recur. The phrasings are written for this corpus.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Pharmacy labels
# --------------------------------------------------------------------------

PHARMACIES = [
    "CITY PHARMACY", "GREENLEAF DRUGS", "UNIVERSITY RX", "OAKMONT CHEMIST",
    "RIVERSIDE PHARMACY", "BROOKFIELD DRUG CO", "HILLCREST APOTHECARY",
    "MAPLE STREET RX", "CENTRAL DISPENSARY", "WESTGATE PHARMACY",
    "ST. ALBAN'S CHEMIST", "LAKESHORE DRUGS", "PINEVIEW PHARMACY",
    "CORNERSTONE RX", "MERIDIAN DRUG", "FAIROAKS PHARMACY",
    "QUAYSIDE CHEMIST", "NORTHFIELD DRUGS", "ELMWOOD PHARMACY",
    "HARBOUR POINT RX", "SUMMIT DISPENSING", "CLEARWATER CHEMIST",
    "BRIDGEPORT PHARMACY", "ASHGROVE DRUG CO",
]

# Generic (INN) names with plausible strengths. Deliberately varied in length
# and letter shape: "Levothyroxine" and "Lisinopril" fail differently, and a
# corpus of only short names hides that. Confusable pairs are included on
# purpose -- a reader that turns HYDRALAZINE into HYDROXYZINE has made the
# kind of mistake that matters here.
DRUGS = [
    ("AMOXICILLIN", "500 MG"), ("LISINOPRIL", "10 MG"),
    ("METFORMIN HCL", "850 MG"), ("ATORVASTATIN", "20 MG"),
    ("LEVOTHYROXINE", "75 MCG"), ("OMEPRAZOLE", "40 MG"),
    ("SERTRALINE", "50 MG"), ("IBUPROFEN", "600 MG"),
    ("AMLODIPINE", "5 MG"), ("PREDNISONE", "20 MG"),
    ("GABAPENTIN", "300 MG"), ("AZITHROMYCIN", "250 MG"),
    ("HYDROCHLOROTHIAZIDE", "25 MG"), ("SIMVASTATIN", "40 MG"),
    ("LOSARTAN POTASSIUM", "50 MG"), ("MONTELUKAST", "10 MG"),
    ("ESCITALOPRAM", "10 MG"), ("PANTOPRAZOLE", "40 MG"),
    ("FUROSEMIDE", "20 MG"), ("METOPROLOL TARTRATE", "25 MG"),
    ("ROSUVASTATIN", "10 MG"), ("TRAZODONE HCL", "100 MG"),
    ("BUPROPION HCL", "150 MG"), ("CITALOPRAM", "20 MG"),
    ("DULOXETINE", "30 MG"), ("FLUOXETINE", "20 MG"),
    ("VENLAFAXINE", "75 MG"), ("CLONAZEPAM", "0.5 MG"),
    ("ALPRAZOLAM", "0.25 MG"), ("LORAZEPAM", "1 MG"),
    ("ZOLPIDEM TARTRATE", "5 MG"), ("CYCLOBENZAPRINE", "10 MG"),
    ("NAPROXEN", "500 MG"), ("MELOXICAM", "15 MG"),
    ("CELECOXIB", "200 MG"), ("TRAMADOL HCL", "50 MG"),
    ("ACETAMINOPHEN", "500 MG"), ("ASPIRIN", "81 MG"),
    ("WARFARIN SODIUM", "2.5 MG"), ("CLOPIDOGREL", "75 MG"),
    ("APIXABAN", "5 MG"), ("RIVAROXABAN", "20 MG"),
    ("ATENOLOL", "50 MG"), ("CARVEDILOL", "12.5 MG"),
    ("DILTIAZEM HCL", "120 MG"), ("VERAPAMIL", "80 MG"),
    ("SPIRONOLACTONE", "25 MG"), ("RAMIPRIL", "5 MG"),
    ("ENALAPRIL MALEATE", "10 MG"), ("VALSARTAN", "80 MG"),
    ("GLIPIZIDE", "5 MG"), ("GLIMEPIRIDE", "2 MG"),
    ("SITAGLIPTIN", "100 MG"), ("PIOGLITAZONE", "30 MG"),
    ("INSULIN GLARGINE", "100 UNITS/ML"), ("LIRAGLUTIDE", "1.2 MG"),
    ("ALLOPURINOL", "300 MG"), ("COLCHICINE", "0.6 MG"),
    ("PREDNISOLONE", "5 MG"), ("METHYLPREDNISOLONE", "4 MG"),
    ("DEXAMETHASONE", "4 MG"), ("HYDROCORTISONE", "10 MG"),
    ("ALBUTEROL SULFATE", "90 MCG"), ("FLUTICASONE", "50 MCG"),
    ("BUDESONIDE", "180 MCG"), ("TIOTROPIUM", "18 MCG"),
    ("CETIRIZINE HCL", "10 MG"), ("LORATADINE", "10 MG"),
    ("FEXOFENADINE", "180 MG"), ("DIPHENHYDRAMINE", "25 MG"),
    ("HYDROXYZINE HCL", "25 MG"), ("HYDRALAZINE HCL", "25 MG"),
    ("RANITIDINE", "150 MG"), ("FAMOTIDINE", "20 MG"),
    ("ONDANSETRON", "4 MG"), ("METOCLOPRAMIDE", "10 MG"),
    ("DOXYCYCLINE HYCLATE", "100 MG"), ("CEPHALEXIN", "500 MG"),
    ("CIPROFLOXACIN", "500 MG"), ("LEVOFLOXACIN", "500 MG"),
    ("CLINDAMYCIN", "300 MG"), ("NITROFURANTOIN", "100 MG"),
    ("TRIMETHOPRIM-SULFA", "800-160 MG"), ("METRONIDAZOLE", "500 MG"),
    ("FLUCONAZOLE", "150 MG"), ("VALACYCLOVIR", "500 MG"),
    ("ACYCLOVIR", "400 MG"), ("OSELTAMIVIR", "75 MG"),
    ("LEVETIRACETAM", "500 MG"), ("LAMOTRIGINE", "100 MG"),
    ("TOPIRAMATE", "50 MG"), ("PHENYTOIN SODIUM", "100 MG"),
    ("CARBAMAZEPINE", "200 MG"), ("VALPROIC ACID", "250 MG"),
    ("DIVALPROEX SODIUM", "500 MG"), ("QUETIAPINE", "25 MG"),
    ("RISPERIDONE", "1 MG"), ("ARIPIPRAZOLE", "5 MG"),
    ("OLANZAPINE", "5 MG"), ("LITHIUM CARBONATE", "300 MG"),
    ("DONEPEZIL HCL", "10 MG"), ("MEMANTINE HCL", "10 MG"),
    ("LEVODOPA-CARBIDOPA", "25-100 MG"), ("ROPINIROLE", "1 MG"),
    ("PREGABALIN", "75 MG"), ("AMITRIPTYLINE", "25 MG"),
    ("NORTRIPTYLINE", "25 MG"), ("MIRTAZAPINE", "15 MG"),
    ("BUSPIRONE HCL", "10 MG"), ("PROPRANOLOL", "40 MG"),
    ("TAMSULOSIN", "0.4 MG"), ("FINASTERIDE", "5 MG"),
    ("SILDENAFIL", "50 MG"), ("OXYBUTYNIN", "5 MG"),
    ("ALENDRONATE SODIUM", "70 MG"), ("RALOXIFENE", "60 MG"),
    ("ESTRADIOL", "1 MG"), ("MEDROXYPROGESTERONE", "10 MG"),
    ("METHOTREXATE", "2.5 MG"), ("AZATHIOPRINE", "50 MG"),
    ("TACROLIMUS", "1 MG"), ("MYCOPHENOLATE", "500 MG"),
    ("POTASSIUM CHLORIDE", "20 MEQ"), ("FERROUS SULFATE", "325 MG"),
    ("FOLIC ACID", "1 MG"), ("CYANOCOBALAMIN", "1000 MCG"),
    ("CHOLECALCIFEROL", "2000 IU"), ("ERGOCALCIFEROL", "50000 IU"),
]

# The safety-critical field, scored on its own. Frequency wording is varied
# because "EVERY 12 HOURS", "TWICE DAILY" and "BID" mean the same thing and
# fail differently, and because a reader that only ever sees "DAILY" learns
# the word rather than the number next to it.
DIRECTIONS = [
    "TAKE 1 TABLET BY MOUTH DAILY",
    "TAKE 2 TABLETS BY MOUTH TWICE DAILY",
    "TAKE 1 CAPSULE EVERY 8 HOURS",
    "TAKE 1 TABLET EVERY 12 HOURS",
    "TAKE 2 CAPSULES BY MOUTH AT BEDTIME",
    "TAKE 1 TABLET THREE TIMES DAILY WITH FOOD",
    "TAKE HALF TABLET BY MOUTH EVERY MORNING",
    "TAKE 1 TABLET BY MOUTH EVERY MORNING",
    "TAKE 1 TABLET BY MOUTH AT BEDTIME",
    "TAKE 2 TABLETS BY MOUTH ONCE DAILY",
    "TAKE 3 TABLETS BY MOUTH DAILY WITH MEALS",
    "TAKE 1 CAPSULE BY MOUTH TWICE DAILY",
    "TAKE 1 CAPSULE EVERY 6 HOURS AS NEEDED",
    "TAKE 1 TABLET EVERY 4 TO 6 HOURS AS NEEDED FOR PAIN",
    "TAKE 2 TABLETS AT ONSET THEN 1 EVERY 8 HOURS",
    "TAKE 1 TABLET DAILY ON AN EMPTY STOMACH",
    "TAKE 1 TABLET 30 MINUTES BEFORE BREAKFAST",
    "TAKE 1 TABLET DAILY WITH A FULL GLASS OF WATER",
    "TAKE HALF TABLET TWICE DAILY FOR 7 DAYS",
    "TAKE 1 TABLET EVERY OTHER DAY",
    "TAKE 1 TABLET DAILY EXCEPT SUNDAY",
    "TAKE 2 TABLETS ON DAY 1 THEN 1 DAILY",
    "TAKE 1 TABLET TWICE DAILY FOR 10 DAYS",
    "TAKE 1 TABLET THREE TIMES DAILY UNTIL FINISHED",
    "TAKE 1 TABLET FOUR TIMES DAILY",
    "TAKE 1 TABLET BY MOUTH WEEKLY",
    "TAKE 1 CAPSULE DAILY IN THE EVENING",
    "TAKE 2 CAPSULES EVERY 12 HOURS WITH FOOD",
    "INJECT 10 UNITS SUBCUTANEOUSLY AT BEDTIME",
    "INHALE 2 PUFFS EVERY 4 HOURS AS NEEDED",
    "INHALE 1 PUFF TWICE DAILY RINSE MOUTH AFTER",
    "APPLY THIN LAYER TO AFFECTED AREA TWICE DAILY",
    "INSTILL 1 DROP IN EACH EYE AT BEDTIME",
    "TAKE 1 TABLET DAILY DO NOT CRUSH OR CHEW",
    "DISSOLVE 1 TABLET UNDER TONGUE AS NEEDED",
    "TAKE 1 TABLET BY MOUTH EVERY 24 HOURS",
    "TAKE 5 ML BY MOUTH THREE TIMES DAILY",
    "TAKE 10 ML BY MOUTH EVERY 8 HOURS",
    "TAKE 1 TABLET DAILY MAY CAUSE DIZZINESS",
    "TAKE 2 TABLETS BY MOUTH 1 HOUR BEFORE PROCEDURE",
]

WARNINGS = [
    "MAY CAUSE DROWSINESS", "TAKE WITH FOOD", "DO NOT DRINK ALCOHOL",
    "FINISH ALL MEDICATION", "AVOID SUNLIGHT",
    "DO NOT CRUSH OR CHEW", "SWALLOW WHOLE", "KEEP REFRIGERATED",
    "SHAKE WELL BEFORE USE", "TAKE ON AN EMPTY STOMACH",
    "MAY CAUSE DIZZINESS", "DO NOT TAKE WITH DAIRY",
    "AVOID DRIVING", "MAY DISCOLOUR URINE",
    "TAKE WITH A FULL GLASS OF WATER", "DO NOT TAKE ANTACIDS",
    "PROTECT FROM LIGHT", "FOR EXTERNAL USE ONLY",
    "KEEP OUT OF REACH OF CHILDREN", "DO NOT STOP SUDDENLY",
    "MAY CAUSE STOMACH UPSET", "AVOID GRAPEFRUIT JUICE",
    "DISCARD 30 DAYS AFTER OPENING", "ROTATE INJECTION SITES",
]

# Invented. Chosen across orthographies because a name is often the only
# proper noun on the label and diacritics fail differently from ASCII.
PATIENTS = [
    "J. MARTINEZ", "A. OKAFOR", "S. NGUYEN", "R. PATEL", "L. ANDERSSON",
    "M. O'CONNELL", "D. KOWALSKI", "T. YAMAMOTO", "F. DUBOIS", "K. SVENSSON",
    "P. RODRIGUEZ", "B. ANDERSON", "C. MACLEOD", "H. SCHMIDT", "N. PETROVA",
    "E. HASSAN", "G. ROSSI", "W. CAMPBELL", "I. KOVACS", "O. ADEYEMI",
    "V. SINGH", "Y. TANAKA", "Z. AHMED", "Q. LIU", "X. CHEN",
    "A. BJORNSSON", "R. DELACROIX", "S. MUELLER", "T. VAN DIJK", "J. KAUR",
    "M. FERNANDEZ", "L. NOVAK", "D. HAUGEN", "C. BLANCHARD", "P. MORALES",
    "K. ABRAHAMS", "B. ILUNGA", "N. MARCHETTI", "E. LINDQVIST", "G. THOMPSON",
]

# --------------------------------------------------------------------------
# Food packaging
# --------------------------------------------------------------------------

PRODUCT_TEXT = [
    ("Diet Cola", "Zero Sugar"), ("Sparkling Water", "Natural Lime"),
    ("Orange Juice", "No Pulp"), ("Whole Milk", "Vitamin D"),
    ("Tomato Soup", "Low Sodium"), ("Greek Yogurt", "Strawberry"),
    ("Energy Drink", "Sugar Free"), ("Almond Butter", "Unsalted"),
    ("Iced Tea", "Lemon"), ("Ginger Ale", "Caffeine Free"),
    ("Root Beer", "Classic Recipe"), ("Lemonade", "Still Cloudy"),
    ("Cold Brew Coffee", "Unsweetened"), ("Coconut Water", "No Added Sugar"),
    ("Apple Juice", "From Concentrate"), ("Cranberry Juice", "100% Juice"),
    ("Oat Milk", "Barista Blend"), ("Soy Milk", "Original"),
    ("Almond Milk", "Vanilla"), ("Chocolate Milk", "Reduced Fat"),
    ("Skimmed Milk", "Fat Free"), ("Double Cream", "Extra Thick"),
    ("Salted Butter", "Churned Daily"), ("Cheddar Cheese", "Mature"),
    ("Cream Cheese", "Original Spread"), ("Cottage Cheese", "Low Fat"),
    ("Plain Yogurt", "Live Cultures"), ("Vanilla Ice Cream", "Made With Cream"),
    ("Chicken Soup", "Hearty Broth"), ("Lentil Soup", "Plant Based"),
    ("Pasta Sauce", "Slow Cooked"), ("Pesto Sauce", "Basil & Pine Nut"),
    ("Baked Beans", "In Tomato Sauce"), ("Chopped Tomatoes", "Italian Grown"),
    ("Sweet Corn", "No Added Salt"), ("Garden Peas", "Frozen Fresh"),
    ("Tuna Chunks", "In Spring Water"), ("Sardines", "In Olive Oil"),
    ("Smoked Salmon", "Scottish"), ("Chicken Breast", "Free Range"),
    ("Breakfast Cereal", "Honey Nut"), ("Bran Flakes", "High Fibre"),
    ("Porridge Oats", "Rolled"), ("Granola", "Maple Pecan"),
    ("Rice Crackers", "Sea Salt"), ("Tortilla Chips", "Lightly Salted"),
    ("Potato Crisps", "Salt & Vinegar"), ("Pretzel Sticks", "Classic"),
    ("Sandwich Crackers", "Cheese"), ("Digestive Biscuits", "Wheatmeal"),
    ("Shortbread Fingers", "All Butter"), ("Chocolate Cookies", "Chunky"),
    ("Peanut Butter Cups", "Milk Chocolate"), ("Almond Crunch Bar", "Dark Chocolate"),
    ("Cereal Bar", "Berry & Yogurt"), ("Protein Bar", "Cookie Dough"),
    ("Trail Mix", "Fruit & Nut"), ("Roasted Cashews", "Lightly Salted"),
    ("Mixed Nuts", "No Peanuts"), ("Sunflower Seeds", "Roasted"),
    ("Sourdough Loaf", "Stone Baked"), ("Wholemeal Bread", "Seeded"),
    ("Gluten Free Rolls", "Soft White"), ("Pitta Bread", "Wholewheat"),
    ("Egg Noodles", "Medium"), ("Penne Pasta", "Durum Wheat"),
    ("Basmati Rice", "Aged"), ("Quinoa", "Tri-Colour"),
    ("Olive Oil", "Extra Virgin"), ("Soy Sauce", "Naturally Brewed"),
    ("Mayonnaise", "Free Range Egg"), ("Mustard", "Wholegrain"),
    ("Tomato Ketchup", "No Added Sugar"), ("Hot Sauce", "Extra Hot"),
]

BRAND_MARKS = ["®", "™", "©"]

# --------------------------------------------------------------------------
# Allergen statements
#
# (lines, allergens that should be reported, hedged?)
#
# An empty set means reporting anything is an invention. `hedged` marks the
# precautionary wording -- "may contain", "traces", "shared equipment" --
# which is a warning about possibility, not a statement of fact, and must
# never be reported with the same certainty as a CONTAINS line.
#
# Categories are the nine the matcher knows: dairy, egg, fish, shellfish,
# peanut, tree nut, soy, gluten, sesame, mustard.
# --------------------------------------------------------------------------

STATEMENTS: list[tuple[list[str], set[str], bool]] = [
    # --- plain CONTAINS: the case that is allowed to act -------------------
    (["CONTAINS: MILK, SOY, WHEAT."], {"dairy", "soy", "gluten"}, False),
    (["ALLERGENS: EGG, FISH."], {"egg", "fish"}, False),
    (["CONTAINS PEANUTS."], {"peanut"}, False),
    (["CONTAINS: TREE NUTS (ALMOND, CASHEW),", "SOY."], {"tree nut", "soy"}, False),
    (["CONTAINS: SHELLFISH (SHRIMP, CRAB)."], {"shellfish"}, False),
    (["CONTAINS: SESAME, MUSTARD."], {"sesame", "mustard"}, False),
    (["CONTAINS: MILK."], {"dairy"}, False),
    (["CONTAINS: WHEAT, BARLEY, RYE."], {"gluten"}, False),
    (["CONTAINS: EGG, MILK, SOY."], {"egg", "dairy", "soy"}, False),
    (["ALLERGEN INFORMATION: CONTAINS PEANUT", "AND TREE NUT."],
     {"peanut", "tree nut"}, False),
    (["CONTAINS: FISH (ANCHOVY)."], {"fish"}, False),
    (["CONTAINS: CRUSTACEANS."], {"shellfish"}, False),
    (["CONTAINS: SESAME SEEDS."], {"sesame"}, False),
    (["CONTAINS: SOYA."], {"soy"}, False),
    (["ALLERGENS: GLUTEN, MILK, EGG,", "MUSTARD."],
     {"gluten", "dairy", "egg", "mustard"}, False),
    (["FOR ALLERGENS SEE INGREDIENTS IN BOLD:", "WHEAT FLOUR, MILK, EGG."],
     {"gluten", "dairy", "egg"}, False),

    # --- derived names: the allergen is present under another word ---------
    (["INGREDIENTS: WHEAT FLOUR, SUGAR, WHEY,", "SOY LECITHIN, SALT."],
     {"gluten", "dairy", "soy"}, False),
    (["INGREDIENTS: DURUM SEMOLINA, WATER,", "EGG ALBUMIN."],
     {"gluten", "egg"}, False),
    (["INGREDIENTS: CASEIN, LACTOSE, SALT."], {"dairy"}, False),
    (["INGREDIENTS: SEMOLINA, WATER, ALBUMEN."], {"gluten", "egg"}, False),
    (["INGREDIENTS: GROUND NUTS, PALM OIL,", "SEA SALT."], {"peanut"}, False),
    (["INGREDIENTS: TAHINI, CHICKPEAS,", "LEMON JUICE, GARLIC."], {"sesame"}, False),
    (["INGREDIENTS: SURIMI, STARCH, CRAB", "EXTRACT."], {"shellfish"}, False),
    (["INGREDIENTS: SPELT FLOUR, WATER, SALT."], {"gluten"}, False),
    (["INGREDIENTS: GHEE, RICE, CARDAMOM."], {"dairy"}, False),
    (["INGREDIENTS: TOFU, EDAMAME, MISO."], {"soy"}, False),
    (["INGREDIENTS: MARZIPAN, SUGAR, EGG WHITE."],
     {"tree nut", "egg"}, False),
    (["INGREDIENTS: WORCESTERSHIRE SAUCE,", "VINEGAR, ONION."], {"fish"}, False),

    # --- hedged: a warning about possibility, never a fact -----------------
    (["MAY CONTAIN PEANUTS AND TREE NUTS."], {"peanut", "tree nut"}, True),
    (["MAY CONTAIN TRACES OF MILK."], {"dairy"}, True),
    (["MADE IN A FACILITY THAT PROCESSES", "PEANUTS AND SOY."],
     {"peanut", "soy"}, True),
    (["MAY CONTAIN TRACES OF NUTS."], {"tree nut"}, True),
    (["PACKED ON EQUIPMENT THAT ALSO", "HANDLES WHEAT."], {"gluten"}, True),
    (["MAY CONTAIN EGG."], {"egg"}, True),
    (["PRODUCED IN A BAKERY HANDLING", "SESAME AND SOYA."],
     {"sesame", "soy"}, True),
    (["MAY CONTAIN SHELL FRAGMENTS AND", "TRACES OF SHELLFISH."],
     {"shellfish"}, True),
    (["CANNOT GUARANTEE NUT FREE."], {"tree nut"}, True),
    (["MAY CONTAIN MILK AND WHEAT."], {"dairy", "gluten"}, True),

    # --- negations: an absence, and reporting one is an invention ----------
    (["DAIRY FREE. GLUTEN FREE."], set(), False),
    (["CONTAINS NO NUTS."], set(), False),
    (["DOES NOT CONTAIN MILK OR EGG."], set(), False),
    (["FREE FROM: PEANUTS, TREE NUTS, SOY."], set(), False),
    (["NUT FREE FACILITY."], set(), False),
    (["NO ARTIFICIAL COLOURS. GLUTEN FREE."], set(), False),
    (["SUITABLE FOR MILK ALLERGY SUFFERERS."], set(), False),
    (["THIS PRODUCT IS FREE FROM WHEAT."], set(), False),
    (["MADE WITHOUT EGGS OR DAIRY."], set(), False),
    (["CERTIFIED GLUTEN FREE. NO SOY."], set(), False),
    (["ALLERGEN FREE RECIPE."], set(), False),
    (["NOT MADE WITH PEANUT OIL."], set(), False),

    # --- plant-milk and lookalike traps ------------------------------------
    (["ALMOND MILK. UNSWEETENED."], {"tree nut"}, False),
    (["OAT MILK BARISTA BLEND."], set(), False),
    (["COCONUT YOGURT ALTERNATIVE."], set(), False),
    (["CASHEW MILK. NO ADDED SUGAR."], {"tree nut"}, False),
    (["RICE MILK. NATURALLY SWEET."], set(), False),
    (["SOY MILK. CALCIUM ENRICHED."], {"soy"}, False),
    (["HAZELNUT SPREAD. NO PALM OIL."], {"tree nut"}, False),
    (["PEANUT FREE SUNFLOWER SPREAD."], set(), False),
    (["BUTTERNUT SQUASH SOUP."], set(), False),
    (["COCONUT MILK. FULL FAT."], set(), False),
    (["NUTMEG AND CINNAMON BLEND."], set(), False),
    (["SHEA BUTTER HAND CREAM."], set(), False),
    (["WATER CHESTNUTS. SLICED."], set(), False),
    (["BUCKWHEAT NOODLES. GLUTEN FREE."], set(), False),

    # --- ingredient lists with nothing to report ---------------------------
    (["INGREDIENTS: CARBONATED WATER, CITRIC", "ACID, NATURAL FLAVOUR."],
     set(), False),
    (["INGREDIENTS: TOMATOES, SALT, BASIL."], set(), False),
    (["INGREDIENTS: POTATOES, SUNFLOWER OIL,", "SEA SALT."], set(), False),
    (["INGREDIENTS: RICE, WATER, SEA SALT."], set(), False),
    (["INGREDIENTS: APPLES, SUGAR, CINNAMON."], set(), False),
    (["INGREDIENTS: CHICKPEAS, WATER, SALT."], set(), False),
    (["INGREDIENTS: OLIVES, BRINE, OREGANO."], set(), False),
]

# Product names used on the allergen labels. The name is a trap in itself:
# "PEANUT BUTTER CUPS" on the front is not an ingredient statement, and a
# reader that treats it as one will report an allergen it never read.
ALLERGEN_PRODUCTS = [
    ("Peanut Butter Cups", "Milk Chocolate"),
    ("Almond Crunch Bar", "Dark Chocolate"),
    ("Sandwich Crackers", "Cheese"),
    ("Breakfast Cereal", "Honey Nut"),
    ("Pasta Sauce", "Basil"),
    ("Rice Crackers", "Sea Salt"),
    ("Chocolate Brownie", "Fudge Centre"),
    ("Oat Cookies", "Raisin"),
    ("Cereal Bar", "Berry Yogurt"),
    ("Veggie Burgers", "Plant Based"),
    ("Fish Fingers", "Cod Fillet"),
    ("Prawn Crackers", "Ready Salted"),
    ("Hummus Dip", "Classic"),
    ("Falafel Bites", "Spiced"),
    ("Sponge Cake", "Vanilla"),
    ("Custard Tart", "Nutmeg"),
    ("Granola Clusters", "Maple"),
    ("Energy Balls", "Cocoa"),
    ("Soup Mix", "Winter Vegetable"),
    ("Stir Fry Sauce", "Sweet Chilli"),
]

# --------------------------------------------------------------------------
# Signage
# --------------------------------------------------------------------------

# What a blind person most needs found, plus the wayfinding and hazard words
# that carry consequences. Room and gate numbers are over-represented because
# a digit misread is the failure that strands someone.
SIGNAGE = [
    "EXIT", "Room 204B", "Keep door closed", "Platform 9", "No Entry",
    "Fire Exit", "Reception", "Way Out", "Staff Only", "Lost Property",
    "Meeting Room 7", "Gate A12", "No Smoking", "Restroom", "Information",
    "Pull", "Push", "Lift", "Elevator", "Stairs",
    "Emergency Exit", "Fire Door Keep Shut", "Assembly Point",
    "First Aid", "Defibrillator", "Wet Floor", "Caution Step",
    "Mind The Step", "Mind The Gap", "Low Ceiling", "Slippery Surface",
    "Wheelchair Access", "Accessible Toilet", "Baby Change",
    "Quiet Room", "Waiting Room", "Consulting Room 3", "Ward 12",
    "Pharmacy", "Outpatients", "X-Ray Department", "Blood Tests",
    "Main Entrance", "Side Entrance", "Goods In", "Deliveries",
    "Car Park", "Level 2", "Basement", "Ground Floor", "Floor 3",
    "Suite 410", "Apartment 7C", "Flat 22", "Unit 15B", "Office 118",
    "Platform 1", "Platform 4B", "Track 9", "Bay 6", "Stand 21",
    "Gate B7", "Gate 34", "Departures", "Arrivals", "Baggage Reclaim",
    "Check In", "Security", "Passport Control", "Customs",
    "Bus Stop", "Taxi Rank", "Ticket Office", "Help Point",
    "Ticket Machine", "Do Not Block", "Keep Clear", "Private",
    "No Public Access", "Authorised Personnel Only", "Danger High Voltage",
    "Flammable", "Corrosive", "Biohazard", "No Naked Flames",
    "Hard Hat Area", "Eye Protection Required", "Construction Site",
    "Closed For Cleaning", "Out Of Order", "Under Maintenance",
    "Open", "Closed", "Opening Hours", "Back In 10 Minutes",
    "Ring Bell For Service", "Please Queue Here", "Form Orderly Queue",
    "Cash Only", "Card Payment Only", "Contactless",
    "Entrance", "No Exit", "This Way", "Alternative Route",
    "Diversion", "Footpath Closed", "Cyclists Dismount",
    "Beware Of The Dog", "Guide Dogs Welcome", "No Dogs Except Guide Dogs",
    "Tactile Paving Ahead", "Crossing Point", "Wait For Signal",
    "Bin", "Recycling", "General Waste", "No Littering",
    "Drinking Water", "Not Drinking Water", "Hot Water",
    "Library", "Canteen", "Cafeteria", "Vending Machine",
    "Locker Room", "Changing Rooms", "Shower",
    "Conference Room B", "Lecture Theatre 2", "Seminar Room 5",
    "Laboratory 9", "Workshop", "Store Room", "Server Room",
]

# Short phrases for the typeface benchmark, which holds everything else fixed.
# Mixed case, digits and a letter-digit collision ("204B" / "2048") on purpose.
FONT_PHRASES = [
    "Diet Cola", "Fire Exit", "Room 204B", "Ginger Ale", "Reception",
    "Gate A12", "Platform 9", "No Entry", "Way Out", "Suite 410",
    "Almond Butter", "Sparkling Water",
]
