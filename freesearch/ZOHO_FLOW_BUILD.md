# Zoho Flow — "TMH Free Search → Leads"

The one piece that lives in Zoho Flow (flow.zoho.eu). Everything hard was
moved OUT of it on purpose: the Edge Function sends `zoho_fields` already in
the module's actual API values, so this flow is a thin pipe — webhook in,
upsert on Email, callback out. Two steps, one function, nothing to maintain
in Flow's editor when the mapping changes.

## Build steps (Flow UI)

1. **Create Flow** → name `TMH Free Search → Leads`.
2. **Trigger: Webhook.** Payload type JSON. Copy the generated URL — that is
   `ZOHO_FLOW_URL` for both Edge Functions.
3. **Action: Custom Function** → paste `tmh_freesearch_upsert` below, map its
   single argument `payload` to the whole webhook body.
4. Turn the flow ON.

## The function (Deluge)

```deluge
string tmh_freesearch_upsert(Map payload)
{
    fields = payload.get("zoho_fields");
    if(fields == null)
    {
        // Old-format push (raw keys only). Nothing to write safely — log and
        // stop rather than guess a mapping here.
        info "no zoho_fields on payload; skipped";
        return "skipped";
    }
    leadId = payload.get("zoho_lead_id");
    isNew = false;
    // Jonathan, 18 Aug: "look for the email within Zoho before creating the
    // search item." Email is unique (case-insensitive) on Leads — this
    // search IS the dedupe. Resolver pushes have no Email and skip it; the
    // Edge Function already fires those at most once per session.
    if(leadId == null || leadId == "")
    {
        em = fields.get("Email");
        if(em != null && em != "")
        {
            found = zoho.crm.searchRecords("Leads", "(Email:equals:" + em + ")");
            if(found.size() > 0)
            {
                leadId = found.get(0).get("id");
            }
        }
    }
    if(leadId != null && leadId != "")
    {
        // UPDATE path. Lead_Status is rep-managed after creation — a human
        // or an existing workflow owns it. Never send it on update.
        fields.remove("Lead_Status");
        resp = zoho.crm.updateRecord("Leads", leadId.toLong(), fields);
    }
    else
    {
        ls = payload.get("lead_status");
        if(ls != null && ls != "")
        {
            fields.put("Lead_Status", ls);   // "New" — creation only
        }
        resp = zoho.crm.createRecord("Leads", fields);
        leadId = resp.get("id");
        isNew = true;
    }
    // Callback — this is what turns two pushes for the same visitor into one
    // lead kept current instead of a duplicate. journey stores the id and
    // sends it back on every later push.
    cb = Map();
    cb.put("zoho_lead_id", leadId.toString());
    cb.put("zoho_lead_url", "https://crm.zoho.eu/crm/tab/Leads/" + leadId);
    if(payload.get("session_id") != null)
    {
        cb.put("session_id", payload.get("session_id"));
    }
    if(payload.get("request_id") != null)
    {
        cb.put("request_id", payload.get("request_id"));
    }
    r = invokeurl
    [
        url : "https://<PROJECT_REF>.supabase.co/functions/v1/journey/zoho-linked"
        type : POST
        parameters : cb.toString()
        headers : {"Content-Type":"application/json"}
    ];
    return "ok:" + leadId + ":" + isNew;
}
```

Replace `<PROJECT_REF>` with the deployed journey function's project ref.

## Contract (what the webhook receives)

Every push carries `record_type` ("lead" | "brand_audit"), the raw session
keys for debugging, and `zoho_fields` — ready-to-write API names and ACTUAL
picklist values, built by `zohoLeadFields()` in journey/index.ts. The 13 new
fields created 18 Aug (Free_Search_Score/Tier/Risk/Conflicts/Session/Tenant/
Date, Email_Source, Locations_Planned, Searched_Tagline, Sells_Via, Seen_Via,
Resale) are verified live under exactly those api_names.

Three sources, distinguishable downstream:

| push | Lead_Source | Email_Source |
|---|---|---|
| report form / AI-gate search | Website - Search | Report Form / AI Gate |
| resolver-found | Search Result Only | Resolver |
| brand audit submitted | Request Brand Audit Website Form | Report Form |

Cerebrum keys its nurture off these — an AI Gate address is not marketing
consent; a Resolver one even less so.

## Why not test with fabricated leads

End-to-end proof uses a real search with a TMH-owned test email, then the
record is inspected and the callback row checked in Supabase — no synthetic
records left in the CRM.
