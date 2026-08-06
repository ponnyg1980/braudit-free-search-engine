# Free Search — API contract (v1)

Two endpoints. Both return JSON. Both are safe to embed in the TMH site, the
Temmy Portal, and approved introducer sites (tenant is a field). Build the
wizard against this doc now — the back-end already conforms.

Base URL is per-deployment. CORS must be restricted in production to the
tenant allow-list; IP rate-limiting sits in front of `/free-search`.

---

## `GET /jurisdictions`

Static picker data for Step 4 / Step 5. Cache it.

```json
{
  "ok": true,
  "picker": {
    "uk_only_shortcut": { "code": "GB", "label": "UK Only" },
    "popular": [
      { "code": "GB", "label": "United Kingdom", "covers": null },
      { "code": "EU", "label": "European Union (EUIPO)", "covers": ["AT","BE", "..."] },
      { "code": "US", "label": "United States", "covers": "all 50 states" },
      { "code": "AU", "label": "Australia", "covers": null },
      { "code": "NZ", "label": "New Zealand", "covers": null },
      { "code": "AE", "label": "Dubai / UAE", "covers": null },
      { "code": "SG", "label": "Singapore", "covers": null },
      { "code": "SA", "label": "Saudi Arabia", "covers": null }
    ],
    "regions":   [ { "code": "WO", "label": "WIPO / Madrid (international)", "covers": "..." } ],
    "all_countries": [ { "code": "AF", "label": "Afghanistan" }, "... A–Z, 152 entries" ],
    "eu_members": ["AT","BE","..."]
  }
}
```

Front-end: render `popular` as chips, `regions` under "Other Regions",
`all_countries` behind a searchable "All Countries A–Z" control. `covers` is
display-only ("EU includes 27 countries…").

---

## `POST /free-search`

### Request — maps 1:1 to the wizard

```json
{
  "name": "MOMENTUS",              // Step 1/2 — required
  "tagline": "Move with us",       // Step 2 — optional (captured, not searched)
  "logo": "data:image/png;base64,iVBOR…",  // Step 2 — optional (captured, not searched)
  "classes": [36],                 // Step 3 — optional (skippable)
  "trading_now": ["GB"],           // Step 4 — office codes
  "planning_to_trade": ["EU","US"],// Step 5 — office codes ([] = none)
  "tenant_id": "tmh"               // who is embedding this
}
```

Field notes:
- `name` is the only required field. No name → `400`.
- `word_marks: []` may carry a *second* word mark (max 3 total incl. name).
- `tagline` and `logo` are **recorded, never searched** — the free tier is UK
  word search only. They exist to (a) qualify the lead and (b) justify the
  audit. The response disclaimers say so.
- `classes` skippable. If empty, every textual match is returned, scored
  without the class-overlap component.
- Country codes validated against the picker's code set; unknown → `400`.

### Response — the results page

```json
{
  "ok": true,
  "status": 200,
  "result": {
    "tenant_id": "tmh",
    "searched_office": "UK IPO",
    "query": { "word_marks": ["MOMENTUS"], "tagline": "Move with us",
               "has_logo": false, "classes": [36] },

    "summary": {
      "total_flagged": 14,      //  Total Results Flagged
      "displayed": 5,           //  Number Displayed
      "high": 3,                //  High Risk
      "medium": 6,              //  Medium Risk
      "low": 5,                 //  Low Risk
      "overall_risk": "High Risk",
      "active_count": 9,
      "truncated": false
    },

    "top_results": [            //  Top 5 registered/active — NO score, band only
      { "risk": "High Risk", "type": "Word", "mark": "MOMENTUS",
        "status": "Registered",
        "company": { "status": "Active", "is_dissolved": false,
                     "sic_codes": ["64999"], "locality": "Manchester" } }
    ],

    "all_results": null,        //  null until unlocked (see gate)
    "gated": false,

    "disclaimers": [
      "These results are provided for information purposes only and do not constitute trademark advice.",
      "Logo and tagline searches are carried out as part of a Brand Audit, not this free UK word search.",
      "You told us you currently trade in GB… This free search covers the UK IPO register only…"
    ],

    "notes": [],

    "cta": {
      "download_report": {
        "label": "Download your full report",
        "requires": ["first_name","last_name","email","phone","consent"],
        "unlocks": "Full conflict list with ownership, company status and goods/services detail."
      },
      "brand_audit": {
        "label": "Request a Brand Audit",
        "blurb": "Covers logos, taglines, social media, marketplaces, domains, company registers and international registers — plus prior-use risk."
      }
    }
  }
}
```

### The gate — 1 free search per IP, then a Free Temmy Account

Enforced by the Supabase Edge Function, not the engine.

- **1st search from an IP (per tenant):** anonymous, allowed. `gated: false` —
  summary counts + the **top-5 registered/active** shortlist (band, type,
  mark, status, and a light company signal — status/sector/town, but **no
  owner name or PII**). The band is shown, never the numeric score.
- **2nd+ search from that IP (no session):** the search does **not run**. The
  endpoint returns `401 account_required` (shape below). The front-end runs the
  email-OTP Free Temmy Account flow, then retries with the session.
- **Signed-in account holder:** unlimited searches, and `gated: true` — the
  full report unlocks (`all_results` with `owner_name`, `company_name`,
  `application_number`, `filing_date`, `classes`, `goods_services`, UKIPO
  link). No extra form: the OTP-verified account already gave us a better lead
  than a form would.

The **account is the lead gate**, moved earlier and email-verified. It's
triggered by a second search *or* a download click — either way the client
lands on the Temmy Portal with a verified email and business info.

#### `401 account_required`

```json
{
  "ok": false, "status": 401, "error": "account_required",
  "message": "You've used your free anonymous search. Create a free Temmy account …",
  "signup": {
    "method": "email_otp",
    "collects": ["first_name","last_name","email","phone","business_name","business_website"],
    "benefits": ["Unlimited free UK searches","Full conflict list with ownership detail",
                 "Save your marks, classes and jurisdictions","Access the Temmy Portal"]
  }
}
```

Front-end: on `account_required`, launch Supabase Auth `signInWithOtp(email)`;
after verification `POST /free-search/account` with the business info, then
re-`POST /free-search` with the `Authorization: Bearer <jwt>` header.

### `POST /free-search/account`

Finalises the Free Temmy Account after email OTP. Requires the auth header.
Persists the business profile (the canonical lead, syncs to Zoho — fast-follow)
and unlocks searching. Body: the `signup.collects` fields plus `trading_now`,
`planning_to_trade`, `consent_marketing`.

### Errors

`{ "ok": false, "status": 400|502, "error": "human-readable message" }`.
`400` = bad payload (fix client-side). `502` = search backend hiccup (retry).
No stack traces ever reach the page.

---

## Wizard → API sequence

| Wizard step | Action |
|---|---|
| 1 Type name → Search | hold `name` |
| 2 Logo / Tagline tickboxes | collect `logo`, `tagline` (optional) |
| 3 Classes & Terms | collect `classes` (skippable) — see term_basket (next build) |
| 4 Trade now | `GET /jurisdictions`, collect `trading_now` |
| 5 Plan to trade | collect `planning_to_trade` |
| Begin Free Search | `POST /free-search` → render results page |
| Download full report | lead form → server unlocks `gated:true` + Zoho/Supabase |
| Request Brand Audit | same lead form → audit pipeline |

The search itself can go live the moment Steps 1–5 + the anonymous results
page are wired. The gate/Zoho unlock is a fast-follow — until it lands, the
Download button collects the lead and we fulfil the report manually.
