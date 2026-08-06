# Free Search → Zoho CRM (Leads) — field mapping spec

Mapped against the **live** Leads module schema (249 fields, pulled 09 Jul
2026). This is what Zoho Flow (or the Edge Function's Zoho step) writes when a
client clicks **Send my report** or **Yes please — create my login**.

Module: **Leads**. Dedupe/upsert key: **Email** (`upsertRecords`, duplicate
check on Email). One lead per person; a repeat searcher updates the same row.

---

## 1. What the free search sends (the lead payload)

From `controller.parse_request` + the wizard lead form:

```json
{
  "first_name":"...", "last_name":"...", "email":"...", "phone":"...",
  "business_name":"...|null", "business_website":"...|null",
  "name":"MOMENTUS",                       // the searched mark
  "tagline":"...|null", "has_logo":false,
  "report_type":"word|logo|combined",
  "classes":[36], "trading_now":["GB"], "planning_to_trade":["EU","US"],
  "overall_risk":"High Risk", "total_flagged":14,
  "consent_marketing":false, "tenant_id":"tmh",
  "portal_account_created":false
}
```

---

## 2. Direct maps — existing fields, no change needed

| Lead payload | Zoho api_name | Zoho label | Notes |
|---|---|---|---|
| first_name | `First_Name` | First Name | |
| last_name | `Last_Name` | Last Name | **system-mandatory** — always send |
| email | `Email` | Email | upsert key |
| phone | `Phone` | Phone | also mirror to `Mobile` if you use WhatsApp/WATI |
| business_name | `Company` | Company | optional; if blank send `"—"` (Zoho likes Company populated) |
| business_website | `Website` | Website | |
| name (searched mark) | `Search_Term` | Search Term | **perfect existing field** |
| name (searched mark) | `word_mark_text` | TME1 Word Mark Text | optional 2nd home so it shows in the TME block |
| business_summary (later, route 2b) | `Business_Summary` | Business Bio | free now, richer once 2b ships |
| classes count | `Number_Of_Classes` | TME1 Number Of Classes | integer |

---

## 3. Picklist maps — existing field, map the VALUE exactly

### `Classes` (multiselectpicklist) — format is `"N (Label)"`

Zoho does **not** accept bare `36`. Send the exact display value. Full lookup:

```
1 (Chemicals)            16 (Artistic Materials)      31 (Agriculture & Livestock)
2 (Colourants)           17 (Piping & Tubing)         32 (Beer & Non-alcoholic Beverages)
3 (Toiletries)           18 (Accessories & Leather)   33 (Alcoholic Beverages)
4 (Fuels)                19 (Construction Materials)  34 (Tobacco & Lighting Articles)
5 (Preparations)         20 (Decorative Fittings & Furniture) 35 (Business Services)
6 (Metal)                21 (Household Utensils)      36 (Financial Services)
7 (Machinery)            22 (Raw Fibres)              37 (Building & Construction)
8 (Tools)                23 (Yarns & Threads)         38 (Telecoms)
9 (Scientific Devices)   24 (Textiles)                39 (Transport)
10 (Therapeutic Devices) 25 (Clothing)                40 (Various Chemical Treatment & Energy Production)
11 (Heating Components)  26 (Accessories Components)  41 (Education)
12 (Vehicles)            27 (Floor & Wall Coverings)  42 (IT)
13 (Weapons & Explosives)28 (Sports Equipment & Toys) 43 (Hospitality)
14 (Jewelery & Precious Minerals) 29 (Raw & Prepared Food) 44 (Animal, Environmental & Human Healthcare)
15 (Musical Instruments) 30 (Convenience Food)        45 (Personal & Social, Legal & Security Services)
```

> The Flow needs a class-number → display-value lookup table. It's static;
> bake it into the Edge Function's Zoho step so Flow receives ready values.

### `Lead_Type` (picklist) — "Trademark Type" ← report_type

| free search | Zoho value |
|---|---|
| word | `Word` |
| logo (figurative) | `Figurative` |
| combined | `Figurative & Text` |
| tagline present | `Strapline` |

### `Lead_Source` (picklist) — use `Website - Search`

Existing values that fit; **standardise on `Website - Search`** for the free
search so reporting is clean. (`Search Result Only` and `Trademark Search Form`
also exist — avoid splitting the funnel across three values.)

### `Lead_Source_Group` (picklist) → `Website Forms`

### `Data_Source` (picklist) → `API`  (the Edge Function writes via API)

### `Locations` (multiselectpicklist) ← trading_now

Current values: `UK | EU | UAE | US | Australia | Canada | New Zealand | South
Africa`. Map office codes → these:

| code | Locations value |
|---|---|
| GB | UK |
| EU | EU |
| US | US |
| AE | UAE |
| AU | Australia |
| CA | Canada |
| NZ | New Zealand |
| ZA | South Africa |

⚠️ **Gap:** the picker offers Singapore, Saudi, WIPO/Madrid and the full A-Z,
but `Locations` has only these 8. See gaps below.

---

## 4. Consent / GDPR

| payload | Zoho | value |
|---|---|---|
| default (soft opt-in) | `Data_Processing_Basis` | `Legitimate Interests` |
| consent_marketing = true | `Data_Processing_Basis` | `Consent - Obtained` |
| consent_marketing = false | `Email_Opt_Out` | `true` |

`Legitimate Interests` aligns with the LIA already on file. Set
`Data_Processing_Basis_Details` to a short note, e.g. "Free trademark search —
soft opt-in, report requested."

---

## 5. Gaps — new fields / picklist values to add

These have no clean home today. Recommend creating them before go-live so no
lead data is silently dropped.

| Need | Recommended field | Type | Why not existing |
|---|---|---|---|
| **Plan-to-trade jurisdictions** | new `Locations_Planned` | multiselectpicklist (same values as Locations) | `Locations` holds only one dimension; "trade now" vs "plan to trade" are different sales signals and you asked to keep both |
| **Missing territories** | add `Singapore`, `Saudi Arabia`, `WIPO / Madrid`, `Other` to **both** Locations picklists | picklist values | picker offers them; Locations doesn't |
| **Tenant / white-label source** | new `Free_Search_Tenant` | text (or picklist: tmh, portal, introducer-x) | no field distinguishes which site produced the lead; critical for introducer attribution |
| **Search risk summary** | new `Free_Search_Risk` (picklist: High/Medium/Low/Negligible) + `Free_Search_Conflicts` (integer) | picklist + integer | lets sales see "High risk, 14 conflicts" at a glance; drives follow-up priority |
| **Tagline searched** | new `Searched_Tagline` | text | no field; or append to `Description` |
| **Report sent / portal created** | new `Free_Report_Sent` + `Temmy_Portal_Created` | datetime each | mirrors the existing `Free_TM_Guide_Downloaded` pattern; powers nurture timing |

Alternative if you'd rather not add fields now: concatenate risk, conflict
count, tagline, tenant and plan-to-trade into `Description` as a labelled block.
Workable, but not reportable — I'd add the fields.

---

## 6. Zoho Flow / write design

1. **Trigger:** the Edge Function `/account` (and `/report`) handler POSTs the
   lead payload to a Zoho Flow webhook — *or* calls Zoho `upsertRecords`
   directly with the service connection. Either way, one write path.
2. **Transform in the Edge Function** (not Flow): class numbers → display
   values, codes → Locations values, risk/type strings → picklist values. Send
   Flow clean, Zoho-ready values so Flow stays a thin pipe.
3. **Upsert on Email.** New email → create; known email → update (a repeat
   searcher enriches their existing lead — new mark into `Search_Term`, classes
   merged).
4. **`portal_account_created=true`** additionally stamps `Temmy_Portal_Created`
   and links the Supabase account id (store in `TemmyID` or a new
   `Supabase_Account_Id` text field).
5. **`expand_for_profiling`** (EU→27) runs here if you want member-level
   territory analytics; otherwise store office-level values in Locations.

---

## 7. Ready-to-send example (post-transform)

What the Edge Function hands Zoho for `upsertRecords` on Leads:

```json
{
  "First_Name":"Jane", "Last_Name":"Doe",
  "Email":"jane@brightbrew.co.uk", "Phone":"+44 7700 900123",
  "Company":"Bright Brew Ltd", "Website":"https://brightbrew.co.uk",
  "Search_Term":"BRIGHT BREW", "word_mark_text":"BRIGHT BREW",
  "Lead_Type":"Word",
  "Classes":["32 (Beer & Non-alcoholic Beverages)","43 (Hospitality)"],
  "Number_Of_Classes":2,
  "Locations":["UK"],
  "Locations_Planned":["EU","US"],
  "Lead_Source":"Website - Search", "Lead_Source_Group":"Website Forms",
  "Data_Source":"API",
  "Data_Processing_Basis":"Legitimate Interests",
  "Free_Search_Risk":"High", "Free_Search_Conflicts":14,
  "Free_Search_Tenant":"tmh",
  "Free_Report_Sent":"2026-07-09T14:12:00+01:00"
}
```

Fields in **bold-gap** (`Locations_Planned`, `Free_Search_Risk`,
`Free_Search_Conflicts`, `Free_Search_Tenant`, `Free_Report_Sent`) must be
created first — see §5.
