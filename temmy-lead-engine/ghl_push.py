#!/usr/bin/env python3
"""
Temmy Lead Engine — direct GoHighLevel (Cerebrum) custom-field push.

Reads the categorical/value TAGS the LinkedIn Cerebrum connector already wrote onto each
`temmy prospect` contact, and copies the value-tags into GHL CUSTOM FIELDS (which can be used
as dynamic merge fields in outreach content). No AI, no tokens — a plain REST push.

Design: the connector writes tags → this script maps tag → field. Handles high-cardinality
values (TM number, mark text) that a per-value workflow branch never could.

USAGE
  python3 ghl_push.py --dry-run      # show what would change, write nothing
  python3 ghl_push.py                # apply

CONFIG — put these in TMH_MASTER.env (or pass --env <path>):
  CEREBRUM_API_KEY=            # GHL Private-Integration / OAuth token, contacts.write scope
  CEREBRUM_LOCATION_ID=        # GHL sub-account / Location ID
  CEREBRUM_API_BASE=https://services.leadconnectorhq.com   # optional; this is the default

Custom-field IDs are DISCOVERED automatically by field name — no need to hand-copy them.
"""
import os, sys, json, time, urllib.request, urllib.error, urllib.parse

API_VERSION = "2021-07-28"
DEFAULT_BASE = "https://services.leadconnectorhq.com"
PROSPECT_TAG = "temmy prospect"

# tag prefix  ->  EXACT GHL custom-field name (matched case/space-insensitively via norm())
TAG_TO_FIELD = {
    "current tm mark: ":     "Current TM Mark Text",
    "current tm status: ":   "Current TM Status",
    "current tm: ":          "Current TM Number",
    "first tm status: ":     "First TM Status",
    "num applications: ":    "Number of Applicants",
    "email found: ":         "Email Found Date",
    "linkedin found: ":      "Linkedin Found Date",
    "phone found: ":         "Phone Found Date",
    "last temmy update: ":   "Last Temmy Update",
}
# tag prefix -> GHL STANDARD field (not a custom field)
TAG_TO_STANDARD = {"organisation: ": "companyName"}

# v3 (2026-07-24): full lead-engine field set — checked for presence at startup so MISSING
# fields are visible before any push (create these in the Cerebrum UI; pushes queue safely
# until they exist). Trademark Type = mark feature (Word/Figurative/Combined…), shared with
# the forensic process — lead engine writes it only via backfill (fill-if-empty) or on
# contacts it created (tag `temmy lead engine`).
CHECK_FIELDS = [
    # 6 Aug 2026 (Jonathan): `mark_name` DELETED in Cerebrum — the word mark now goes to
    # `Current TM Mark Text`. Removed from this list so the startup check stops reporting a
    # false MISSING; a check that always fails is a check people learn to ignore.
    "IPO Applicant ID", "applicant_name", "TM_Number",   # trademark_name deleted 6 Aug
    "Current TM Mark Text", "First TM Status", "Current TM Status", "Number of Applicants",
    "Registration Date", "Expiry Date", "Register Country",
    "rep-status", "Rep Name", "Rep ID", "event-type", "event_date",
    "Trademark Type", "Company Number", "CH Trading Status",
    "Trademark Count", "Trademark Number List", "Trademark Name List",
    # "Classes"/"Class Descriptions" live on the TM Class OBJECT, not the contact (24 Jul pm7)
    "Portal Link",
    "Phoenix Company", "Phoenix Company Number", "Brand In Use",
]

# queue/runbook field name -> actual Cerebrum field name (normalised via norm()).
# The runbook says "Current TM Number" but the field in Cerebrum is "TM Number" (2026-07-22).
FIELD_ALIASES = {"currenttmnumber": "tmnumber",
                 # 2026-07-24: field created as "CH Trading Status" — accept the runbook name too
                 "tradingstatus": "chtradingstatus"}
# note: "email: valid/not found" and "match: …" etc. remain TAGS only, not pushed to fields.

# ── v2 (2026-07-20): QUEUE MODE ────────────────────────────────────────────────
# The operator no longer writes unique values as tags. Instead each run appends entries to
# field_push_queue.json:  [{"contact_id": "...", "fields": {"<GHL Field Name>": "value", ...},
#                           "standard": {"companyName": "..."}}, ...]
# This script pushes queue entries FIRST (removing them on success), then falls back to the
# legacy tag-scan for contacts synced before v2. Once legacy tags are deleted the tag-scan
# simply finds nothing. Field names are resolved case/space-insensitively via norm(), so use
# the names as created in Cerebrum, e.g.: Current TM Mark Text, Current TM Number,
# Current TM Status, First TM Status, Number of Applicants, Email Found Date,
# Linkedin Found Date, Phone Found Date, Last Temmy Update, Rep Status, Rep Name, Classes,
# Event Type, Event Date, Company Number.
QUEUE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "field_push_queue.json")

def norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

def load_env(path):
    cfg = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("=")
        cfg[k.strip()] = v.strip()
    return cfg

def find_env(explicit=None):
    cands = [explicit] if explicit else []
    cands += [
        os.path.expanduser("~/Documents/Claude/Projects/TMHHQ/TMH_MASTER.env"),
        os.path.expanduser("~/Documents/Claude/TMHHQ/TMH_MASTER.env"),
        os.path.join(os.path.dirname(__file__), "..", "..", "TMH_MASTER.env"),
        os.path.join(os.path.dirname(__file__), "TMH_MASTER.env"),
        os.path.join(os.path.dirname(__file__), "..", "temmy-access", "secrets.env"),
    ]
    for c in cands:
        if c and os.path.exists(c): return c
    raise SystemExit("TMH_MASTER.env not found. Pass --env <path> or connect the folder holding it.")

def api(base, key, method, path, body=None, params=None):
    url = base.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Version": API_VERSION,
        "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 + attempt * 2); continue
            raise SystemExit(f"HTTP {e.code} on {method} {path}: {e.read().decode()[:300]}")
    raise SystemExit(f"rate-limited on {method} {path}")

def get_field_map(base, key, loc):
    d = api(base, key, "GET", f"/locations/{loc}/customFields")
    fields = d.get("customFields") or d.get("customField") or []
    by_norm = {norm(f.get("name")): f.get("id") for f in fields if f.get("id")}
    for alias, target in FIELD_ALIASES.items():
        if alias not in by_norm and target in by_norm:
            by_norm[alias] = by_norm[target]
    id_type = {f.get("id"): (f.get("dataType") or f.get("type") or "").upper() for f in fields if f.get("id")}
    return by_norm, id_type, fields

def fmt_value(val, dtype):
    """Coerce a tag string into the shape the GHL field type expects."""
    if not val: return val
    if dtype in ("NUMERICAL", "NUMBER", "MONETORY", "MONETARY"):
        digits = "".join(ch for ch in val if (ch.isdigit() or ch == "."))
        return digits or val
    if dtype in ("DATE", "DATE_PICKER", "DATEPICKER"):
        return val  # ISO 'YYYY-MM-DD'; if GHL rejects, switch to epoch-ms (see README note)
    return val

def get_contact(base, key, cid):
    d = api(base, key, "GET", f"/contacts/{cid}")
    return d.get("contact") or d

# ── BRIEF_2 build step 2a: route-aware dedupe (ENGINE_STRUCTURE.md "COLLISION RISK") ──────────
# `IPO Applicant ID` is the engine's dedupe key everywhere (master_sync.py, route2_push.py, this
# file's iter_prospect_contacts). The rep contract (step 2c) puts a CLIENT's applicant id on a
# REP's contact (the rep's "anchor" trademark), so a plain aid lookup can match the rep instead of
# the applicant and overwrite a person at e.g. Herrero & Asociados with the applicant's details.
# Two people, one record, no error raised. Fix: exclude rep contacts from any aid-based lookup.
#
# ⚠️ DEVIATION FROM THE BRIEF'S LITERAL TEXT, DELIBERATE: ENGINE_STRUCTURE.md says to exclude on
# the `route:` tag (`route: 1ba-rep-en` / `route: 1bb-rep-other`). But route_runner.py's
# build_rep_batch (ported from the original route1_dispatch.py, unchanged in that respect) tags a
# rep's CLIENT contacts with that SAME route: tag — only "target: representative" vs
# "target: applicant" tells the two apart. Excluding on the route: tag alone would wrongly exclude
# every genuine Route 1b client (applicant) contact from aid-based dedupe too, causing duplicate
# client contacts — a different bug, not a fix. `target: representative` already exists in the live
# data (no_aid_contacts.csv's 15 unjoined rep contacts all carry it) and is the correct, precise
# signal, so that is what this excludes on. Flagged to Jonathan; not silently substituted.
REP_TARGET_TAG = "target: representative"

def _contact_is_rep(base, key, c):
    """True if a /contacts/search result is a REP contact (see REP_TARGET_TAG above). List-endpoint
    results don't reliably include `tags` (same gap iter_prospect_contacts() works around), so fetch
    the full contact when needed."""
    tags = c.get("tags")
    if tags is None:
        c = get_contact(base, key, c.get("id")) or c
        tags = c.get("tags") or []
    return any((t or "").strip().lower() == REP_TARGET_TAG for t in tags)

def find_by_aid(base, key, loc, aid, aid_fid, page_limit=10):
    """Route-aware `IPO Applicant ID` lookup — EXCLUDES rep contacts (see REP_TARGET_TAG). Rep
    contacts dedupe on Rep ID instead (find_by_repid). Returns the first non-rep match's contact_id,
    or None. This must be the ONLY way any script looks up an existing contact by aid — a raw
    /contacts/search by customFields.<aid> without this filter reintroduces the collision bug."""
    if not (aid and aid_fid):
        return None
    try:
        d = api(base, key, "POST", "/contacts/search", body={
            "locationId": loc, "page": 1, "pageLimit": page_limit,
            "filters": [{"field": f"customFields.{aid_fid}", "operator": "eq", "value": str(aid)}]})
        for c in (d.get("contacts") or []):
            if not _contact_is_rep(base, key, c):
                return c.get("id")
        return None
    except Exception:
        return None

def find_by_repid(base, key, loc, rep_id, repid_fid):
    """Rep-contact dedupe key — Rep ID, not IPO Applicant ID (BRIEF_2 step 2a). Note "has a Rep ID"
    is NOT itself a usable discriminator (an applicant contact carries Rep ID too, whenever that
    applicant has a representative) — this function is only for looking up a REP's own contact by
    its own Rep ID, never for deciding whether some other contact IS a rep."""
    if not (rep_id and repid_fid):
        return None
    try:
        d = api(base, key, "POST", "/contacts/search", body={
            "locationId": loc, "page": 1, "pageLimit": 5,
            "filters": [{"field": f"customFields.{repid_fid}", "operator": "eq", "value": str(rep_id)}]})
        cs = d.get("contacts") or []
        return cs[0].get("id") if cs else None
    except Exception:
        return None

# ── DAILY_CYCLE_INSTRUCTIONS.md Step 3 (5 Aug) — "remove now-invalid tags and add current
# ones... sync REPLACES tags, so always send the complete set." Root cause found while building
# this: at least two scripts (master_sync.py, route2_push.py) were PUTting a freshly-constructed,
# route-local tags list straight onto EXISTING contacts on every update — since Cerebrum's PUT
# replaces the whole tags array (CEREBRUM_DEVELOPER_BRIEF.md §1), this SILENTLY WIPED every tag
# that contact already had: another route's `route:` provenance tag ("set once, never rewritten"),
# `tm status:`, `Rep - DO NOT CONTACT`, DND, everything — any time that contact happened to also be
# touched by master_sync.py or route2_push.py after being created/tagged elsewhere. This is the
# fix, in the ONE place a tag update should ever happen, so it cannot be reintroduced per-script.
def replace_tags_safely(base, key, cid, add_tags, remove_tags=None, current_tags=None):
    """The only sanctioned way to update tags on an EXISTING contact. Fetches (or accepts
    pre-fetched) current tags, drops `remove_tags` (tags THIS caller is authoritative over and
    knows are now stale — e.g. its own prior `tm status: *` value; never blanket-clear an unrelated
    prefix, that just moves the clobbering problem instead of fixing it), adds `add_tags`
    (case/space-insensitive dedupe against what's already there), and PUTs the resulting COMPLETE
    set. Returns the new tag list actually sent, for logging."""
    if current_tags is None:
        c = get_contact(base, key, cid) or {}
        current_tags = c.get("tags") or []
    remove_norm = {norm(t) for t in (remove_tags or [])}
    kept = [t for t in current_tags if norm(t) not in remove_norm]
    seen = {norm(t) for t in kept}
    final = list(kept)
    for t in (add_tags or []):
        if norm(t) not in seen:
            final.append(t)
            seen.add(norm(t))
    api(base, key, "PUT", f"/contacts/{cid}", body={"tags": final})
    return final


# ── CEREBRUM_DEVELOPER_BRIEF.md §4 — fields the engine must NEVER write. These are Cerebrum-owned;
# human decisions live there (a staff member marking a rep re-engaged would be silently undone the
# next morning if the engine ever wrote over it). One deny-list, checked at the ONE place every
# route's field push actually flows through (main()'s queue consumer below) — so a route script
# constructing its own field dict cannot reintroduce this by simply not knowing about the rule.
# ⚠️ Exact strings per the brief — do not "correct" them: `Responds to` is lower-case `to`.
CEREBRUM_OWNED_FIELDS = {norm(f) for f in (
    "Rep Comms Status", "Applicant Comms Status", "Responds to",
    "Rep Contact Attempts", "Applicant Contact Attempts",
    "Last Rep Contact Date", "Last Applicant Contact Date",
)}


def strip_cerebrum_owned(fields, context=""):
    """Returns (clean_fields, violations). Never raises — a push with 9 legitimate fields and 1
    forbidden one should still land the 9, loudly minus the 1, not have the whole update dropped.
    `violations` is a list of (field_name, context) tuples; the caller must print/log it if
    non-empty — this function only strips, it does not itself make the violation visible."""
    clean, violations = {}, []
    for k, v in (fields or {}).items():
        if norm(k) in CEREBRUM_OWNED_FIELDS:
            violations.append((k, context))
        else:
            clean[k] = v
    return clean, violations


# ── D12 (6 Aug 2026, OPEN_DEFECTS.md) — VISIBILITY ONLY, no strip/clear. linkedin_sync_one has
# been seen writing a "...@placeholderemail.com" address as if it were a real result. The first
# D12 build detected AND stripped/cleared these; Jonathan corrected that same day: "We leave
# placeholder emails." strip_placeholder_email() and fix_placeholder_email_contact() (the
# strip-before-push and clear-existing-damage halves) have been REMOVED — see OPEN_DEFECTS.md D12's
# "CORRECTED" note for the full history, not deleted, marked. What remains is read-only: detection
# + counting, the original "Exceptions rule counting contacts holding a placeholder" ask, which is
# still wanted — nothing here mutates a contact.
PLACEHOLDER_EMAIL_SUFFIX = "@placeholderemail.com"


def is_placeholder_email(addr):
    return bool(addr) and str(addr).strip().lower().endswith(PLACEHOLDER_EMAIL_SUFFIX)


def find_placeholder_email_contacts(base, key, loc, page_limit=100):
    """D12 Exceptions rule — live read against Cerebrum: every contact whose native `email` field
    ends in @placeholderemail.com. Same /contacts/search "contains" filter shape
    iter_prospect_contacts() already uses for tags, applied to the standard email field instead. A
    genuine Cerebrum-side smart list can be built off the identical filter natively; this is the
    engine-side equivalent, so the count is visible without one existing yet."""
    out, page = [], 1
    while True:
        d = api(base, key, "POST", "/contacts/search", body={
            "locationId": loc, "page": page, "pageLimit": page_limit,
            "filters": [{"field": "email", "operator": "contains",
                        "value": PLACEHOLDER_EMAIL_SUFFIX}]})
        contacts = d.get("contacts") or []
        if not contacts:
            break
        out.extend(contacts)
        if len(contacts) < page_limit:
            break
        page += 1
    return out



def iter_prospect_contacts(base, key, loc):
    """Yield full contacts carrying the `temmy prospect` tag, via the v2 search endpoint."""
    page = 1
    while True:
        d = api(base, key, "POST", "/contacts/search", body={
            "locationId": loc, "page": page, "pageLimit": 100,
            "filters": [{"field": "tags", "operator": "contains", "value": PROSPECT_TAG}]})
        contacts = d.get("contacts") or []
        if not contacts: break
        for c in contacts:
            if "tags" not in c or "customFields" not in c:
                c = get_contact(base, key, c.get("id")) or c
            yield c
        if len(contacts) < 100: break
        page += 1; time.sleep(0.2)

def build_update(contact, field_norm2id, id_type):
    tags = contact.get("tags") or []
    cf = []; std = {}
    for t in tags:
        tl = t.lower()
        for pref, fname in TAG_TO_FIELD.items():
            if tl.startswith(pref):
                val = t[len(pref):].strip()
                fid = field_norm2id.get(norm(fname))
                if fid and val:
                    cf.append({"id": fid, "value": fmt_value(val, id_type.get(fid, ""))})
                break
        for pref, sfield in TAG_TO_STANDARD.items():
            if tl.startswith(pref):
                std[sfield] = t[len(pref):].strip()
    body = {}
    if cf: body["customFields"] = cf
    body.update(std)
    return body

def main():
    dry = "--dry-run" in sys.argv
    env_path = None
    if "--env" in sys.argv: env_path = sys.argv[sys.argv.index("--env") + 1]
    cfg = load_env(find_env(env_path))
    key = cfg.get("CEREBRUM_API_KEY"); loc = cfg.get("CEREBRUM_LOCATION_ID")
    base = cfg.get("CEREBRUM_API_BASE", DEFAULT_BASE)
    if not key or not loc:
        raise SystemExit("CEREBRUM_API_KEY and CEREBRUM_LOCATION_ID must be set in the env file.")
    field_norm2id, id_type, fields = get_field_map(base, key, loc)
    print(f"Discovered {len(field_norm2id)} custom fields. Target fields present:")
    for fname in list(dict.fromkeys(list(TAG_TO_FIELD.values()) + CHECK_FIELDS)):
        print(f"  {'OK ' if norm(fname) in field_norm2id else 'MISSING'} {fname}")

    # ── D12 (6 Aug 2026, OPEN_DEFECTS.md) — standalone REPORT mode, read-only, safe to run any
    # time. CORRECTED same day: Jonathan — "We leave placeholder emails." There used to be a
    # --fix-placeholder-emails mode that cleared them; removed (see OPEN_DEFECTS.md D12's
    # "CORRECTED" note). This still counts/lists them — the original "Exceptions rule" ask — it
    # just never mutates anything.
    if "--find-placeholder-emails" in sys.argv:
        hits = find_placeholder_email_contacts(base, key, loc)
        print(f"\nD12 Exceptions (visibility only, not touched) — {len(hits)} contact(s) currently "
             f"hold a '{PLACEHOLDER_EMAIL_SUFFIX}' address:")
        for c in hits:
            print(f"  {c.get('id')}  {c.get('firstName','')} {c.get('lastName','')}  "
                 f"email={c.get('email')}")
        return
    budget = 38.0
    if "--max-seconds" in sys.argv: budget = float(sys.argv[sys.argv.index("--max-seconds")+1])
    donef = os.path.join(os.path.dirname(__file__), "ghl_pushed.json")
    done = set(json.load(open(donef))) if (not dry and os.path.exists(donef)) else set()
    t0 = time.time(); n = updated = skipped = 0

    # ── v2: process the field-push queue first ────────────────────────────────
    q_pushed = 0
    owned_violations_total = 0
    if os.path.exists(QUEUE_FILE):
        try:
            queue = json.load(open(QUEUE_FILE))
        except Exception as e:
            print(f"WARNING: could not parse {QUEUE_FILE}: {e}"); queue = []
        remaining = []
        for entry in queue:
            cid = entry.get("contact_id")
            if not cid: continue
            # CEREBRUM_DEVELOPER_BRIEF.md §4 deny-list — checked HERE because every route's field
            # push flows through this one loop. See strip_cerebrum_owned()'s docstring above.
            safe_fields, violations = strip_cerebrum_owned(entry.get("fields") or {}, context=f"contact {cid}")
            if violations:
                owned_violations_total += len(violations)
                for fname, ctx in violations:
                    print(f"  🚫 REFUSED Cerebrum-owned field '{fname}' for {ctx} — never written by "
                         "the engine, see CEREBRUM_DEVELOPER_BRIEF.md §4. Dropped from this push, "
                         "the rest of the entry's fields still proceed.")
            # D12 — CORRECTED 6 Aug: this used to strip a placeholder email here before push.
            # Jonathan: "We leave placeholder emails." Removed — entry["standard"]/fields go
            # through unchanged; find_placeholder_email_contacts() still reports them read-only.
            cf = []
            for fname, val in safe_fields.items():
                fid = field_norm2id.get(norm(fname))
                if fid and val not in (None, ""):
                    cf.append({"id": fid, "value": fmt_value(str(val), id_type.get(fid, ""))})
                elif not fid:
                    print(f"  MISSING FIELD in Cerebrum: '{fname}' (contact {cid}) — kept in queue")
            body = {}
            if cf: body["customFields"] = cf
            body.update({k: v for k, v in (entry.get("standard") or {}).items() if v})
            if not body: continue
            if dry:
                print(f"[dry-queue] {cid} -> {body}"); remaining.append(entry); continue
            if time.time() - t0 > budget:
                remaining.append(entry); continue
            try:
                api(base, key, "PUT", f"/contacts/{cid}", body=body)
                q_pushed += 1; time.sleep(0.1)
            except SystemExit as e:
                print(f"  queue push failed for {cid}: {e} — kept in queue"); remaining.append(entry)
        if not dry:
            json.dump(remaining, open(QUEUE_FILE, "w"), indent=1)
        print(f"queue mode: pushed {q_pushed}, {len(remaining)} left in queue")
    # ── legacy tag-scan below (contacts synced before v2; no-op once tags are deleted) ──
    if "--queue-only" in sys.argv:
        print(f"\n{'DRY-RUN: ' if dry else ''}queue pushed: {q_pushed} | --queue-only: skipped legacy tag-scan")
        return
    for c in iter_prospect_contacts(base, key, loc):
        cid = c.get("id"); n += 1
        if cid in done: skipped += 1; continue
        body = build_update(c, field_norm2id, id_type)
        if not body: continue
        if dry:
            print(f"[dry] {cid} {c.get('firstName','')} {c.get('lastName','')} -> "
                  f"{ {**{f['id']:f['value'] for f in body.get('customFields',[])}, **{k:v for k,v in body.items() if k!='customFields'}} }")
        else:
            api(base, key, "PUT", f"/contacts/{cid}", body=body)
            done.add(cid); updated += 1; time.sleep(0.1)
            if updated % 20 == 0: json.dump(sorted(done), open(donef, "w"))
        if not dry and time.time() - t0 > budget:
            json.dump(sorted(done), open(donef, "w"))
            print(f"time budget hit — pushed {updated} this run, {skipped} already done; re-run to continue.")
            break
    if not dry: json.dump(sorted(done), open(donef, "w"))
    print(f"\n{'DRY-RUN: ' if dry else ''}queue pushed: {q_pushed} | tag-scan seen: {n} | updated this run: {updated} | already done: {skipped} | total done: {len(done) if not dry else n}")
    if owned_violations_total:
        print(f"🚫 {owned_violations_total} Cerebrum-owned field write(s) REFUSED this run — see "
             "CEREBRUM_DEVELOPER_BRIEF.md §4. Investigate the producing script(s); this should be 0.")

if __name__ == "__main__":
    main()
