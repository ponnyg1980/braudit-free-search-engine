# Class-selection tools — standalone API (for the site, the portal, and Zoho)

Every class-selection tool is a standalone HTTP endpoint returning clean JSON.
The same endpoints power the client-facing wizard, the standalone widget
(`web/class-assistant.html`), and — the point of this doc — **buttons and
custom functions inside Zoho** so the team gets the same help picking classes.

All run on the fast paths (standard API + Query Runs), so they're safe to call
synchronously from a Zoho button. Base URL is the deployed engine / Edge
Function; examples use `{BASE}`.

Common envelope: `{ "ok": true, "status": 200, ... }` on success;
`{ "ok": false, "status": 4xx|5xx, "error": "..." }` on failure.

---

## The tools

| Tool | Endpoint | Input | Gives you |
|---|---|---|---|
| Trademark lookup | `GET {BASE}/lookup/marks?q=` | name or number | list: number, name, status, applicant |
| Trademark detail | `GET {BASE}/lookup/mark?number=` | app number | classes + per-class specification |
| Owner lookup | `GET {BASE}/lookup/owners?q=` | name or IPO id | list: name, address, postcode, #marks |
| Owner portfolio | `GET {BASE}/lookup/owner?id=` | IPO id | that owner's trademarks (number/name/status/classes) |
| **Classes from a trademark** | `GET {BASE}/lookup/basket?number=` | app number | `term_basket`: classes + terms |
| **Classes from SIC** | `GET {BASE}/lookup/sic?q=` | SIC code(s) | classes banded (primary/common/sometimes) + basket |
| **Classes by banding** | `GET {BASE}/lookup/band?id=` **or** `?numbers=` | owner id OR app-number list | classes+terms banded Essential→Optional + basket |

The three **bold** tools are the class-selection assistants. The lookups above
them feed those (find the trademark/owner first, then pull classes).

---

## 1. Classes from a competitor trademark

`GET {BASE}/lookup/basket?number=UK00003439365`

```json
{ "ok": true,
  "basket": {
    "source_type": "competitor_trademark", "source_ref": "UK00003439365",
    "classes": [9, 36, 42],
    "entries": [
      { "nice_class": 36, "heading": "Financial…",
        "terms": [ {"text":"Banking services","kept":true}, … ] }, … ] } }
```

## 2. Classes from SIC codes

`GET {BASE}/lookup/sic?q=62012,47110`

```json
{ "ok": true, "input_sics": ["62012","47110"], "unmatched": [],
  "classes": [
    {"nice_class":35,"band":"primary","heading":"…","from_sic":["47110"]},
    {"nice_class":42,"band":"primary","heading":"…","from_sic":["62012"]},
    {"nice_class":9, "band":"common", "heading":"…","from_sic":["62012"]} ],
  "basket": { … term_basket … } }
```

## 3. Classes by frequency-banding real marks

Band a competitor's whole portfolio:
`GET {BASE}/lookup/band?id=1313282`

…or a hand-picked set of marks:
`GET {BASE}/lookup/band?numbers=UK00003439365,UK00003293438`

```json
{ "ok": true, "n_marks": 30,
  "labels": {"a":"Essential","b":"Recommended","c":"Worth considering","d":"Optional"},
  "classes": [
    {"nice_class":25,"band":"a","label":"Essential","count":20,"share":0.67,
     "heading":"Clothing…",
     "terms":[{"text":"articles of outer clothing","band":"a","label":"Essential","count":16}, …]},
    {"nice_class":41,"band":"b","label":"Recommended","count":16, …}, … ],
  "owner": { "name":"Gymshark Limited", … },
  "basket": { … Essential+Recommended kept … } }
```

---

## Calling from Zoho (Deluge `invokeurl`)

Drop this into a **custom function** or a **button** on the Lead/Deal. Example:
map a lead's Company/IPO id to suggested classes and write them to the `Classes`
multi-select.

```javascript
// classesFromCompetitorPortfolio(ipoId)
resp = invokeurl
[
    url : "{BASE}/lookup/band?id=" + ipoId
    type : GET
];
classes = List();
for each c in resp.get("classes")
{
    // Zoho 'Classes' picklist values are "N (Label)" — see ZOHO_FIELD_MAPPING.md
    if(c.get("band") == "a" || c.get("band") == "b")   // Essential + Recommended
    {
        classes.add(c.get("nice_class"));
    }
}
info classes;   // -> add to the record's Classes field
```

SIC route (deterministic, instant):

```javascript
resp = invokeurl [ url : "{BASE}/lookup/sic?q=" + sicCodes  type : GET ];
info resp.get("classes");
```

Competitor trademark route:

```javascript
resp = invokeurl [ url : "{BASE}/lookup/basket?number=" + tmNumber  type : GET ];
info resp.get("basket").get("classes");
```

Notes for the Zoho build:
- URL-encode free text (`zoho.encryption.urlEncode(q)`).
- The `Classes` picklist stores `"36 (Financial Services)"` not `36` — map the
  number to the display value (lookup table in `ZOHO_FIELD_MAPPING.md`).
- All three class tools also return a ready `basket` object if you'd rather
  store terms as well as classes.
- CORS/allow-list: add your Zoho domain to the Edge Function `ALLOWED_ORIGINS`
  if calling from client-side widgets; server-side `invokeurl` is unaffected.

---

## Not yet standalone (documented so nobody wires them prematurely)

- **Goods/services text search** ("find marks that sell X") — works, but the
  `ILIKE` across the 2.85M-row specification table is ~25s. Needs a materialised
  or full-text-indexed approach before it's a live tool. The AI description /
  website routes depend on it.
- **SIC empirical banding** — the `/lookup/sic` concordance can be upgraded to
  real filing frequencies via Query Runs; same endpoint, richer data.
