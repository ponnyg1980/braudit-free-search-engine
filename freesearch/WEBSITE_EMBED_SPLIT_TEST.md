# TMH Search Box — Split-Test Embed Pack (for the website developers)

20 Aug 2026. Two search boxes, one per journey, for A/B split testing on
www.thetrademarkhelpline.com (home page and the Free Trademark Search page).
Both capture the brand name, log it server-side BEFORE navigating (an
abandoned visitor still leaves a journey record), then hand the visitor to
the right wizard with the name carried across — nothing is re-typed.

## The two snippets

**Variant A — Free Search** (full journey: Name → Searches → Jurisdictions →
Classes → Results). Hands off to the wizard ON THE DOMAIN
(www.thetrademarkhelpline.com/free-search/), landing on **Searches**:

```html
<script src="https://braudit-free-search.onrender.com/embed.js"
        data-widget="search-box"
        data-variant="free"
        data-target="https://www.thetrademarkhelpline.com/free-search/"
        async></script>
```

**Variant B — Quick Search** (short journey: Name → Classes → Results).
Hands off to www.thetrademarkhelpline.com/uk-trademark-quick-search/,
landing on **Classes**:

```html
<script src="https://braudit-free-search.onrender.com/embed.js"
        data-widget="search-box"
        data-variant="quick"
        data-target="https://www.thetrademarkhelpline.com/uk-trademark-quick-search/"
        async></script>
```

Both wizard pages went live 20 Aug (WordPress pages 7466/7467, each a
single Custom-HTML block loading the wizard via embed.js). The visitor's
address bar stays on thetrademarkhelpline.com for the whole journey; the
hand-off parameters (session, screen, name) pass through the page URL into
the embedded wizard automatically.

Place each snippet wherever the box should render — it injects an iframe in
place and auto-sizes its height. Nothing else is required.

## Slim bar versions (same journeys, single-line)

Add `data-style="bar"` and the widget renders as one line — just the input
and the Search button, no card, no heading. For hero strips and banners
where the page already provides the context. Identical logging and
hand-off, so A/B numbers stay comparable with the boxed versions.

**Bar — Free Search:**

```html
<script src="https://braudit-free-search.onrender.com/embed.js"
        data-widget="search-box"
        data-variant="free"
        data-style="bar"
        data-target="https://www.thetrademarkhelpline.com/free-search/"
        async></script>
```

**Bar — Quick Search:**

```html
<script src="https://braudit-free-search.onrender.com/embed.js"
        data-widget="search-box"
        data-variant="quick"
        data-style="bar"
        data-target="https://www.thetrademarkhelpline.com/uk-trademark-quick-search/"
        async></script>
```

The bar sits on a transparent background and stretches to its container's
width — constrain it with the surrounding column/section as usual.
Verified live 20 Aug: bar → session minted + name logged → hand-off to the
domain wizard page with the session adopted.

## What each variant does

| | Variant A (free) | Variant B (quick) |
|---|---|---|
| Heading shown | Free Trademark Search | UK Trademark Quick Search |
| Journey logged as | `free_search` | `quick_search` |
| Zoho Lead Source | Free Search | Quick Search |
| Hands off to | `/free-search` | `/uk-trademark-quick-search` |
| Visitor lands on | Searches page (step 2 of 5) | Classes page (step 2 of 3) |

The Lead Source split is what makes the A/B readable in Zoho: every lead
carries the journey it came through, end to end.

## Optional attributes

- `data-target="https://…"` — override where the wizard lives. Unset, each
  variant goes to its own page on the engine host
  (braudit-free-search.onrender.com). Set this later if the wizards are
  proxied onto thetrademarkhelpline.com paths.
- `data-tenant="tmh"` — partner/tenant id (defaults to `tmh`).
- `data-journey="https://…"` — journey logging endpoint override. Defaults
  to production; leave unset.

## Direct links (no box)

For buttons or menu items that should enter a journey directly:

- Free Search: `https://braudit-free-search.onrender.com/free-search`
- Quick Search: `https://braudit-free-search.onrender.com/uk-trademark-quick-search`

## Notes for the split test

- Serve ONE variant per visitor (your A/B tool decides which snippet
  renders). Both boxes on the same page at once will muddy the test.
- The box navigates the top-level page on submit (it posts a message out of
  its iframe; embed.js performs the navigation). Don't sandbox the iframe.
- Content-Security-Policy: if the site sets one, allow frames from and
  navigation to `braudit-free-search.onrender.com`, and the iframe itself
  connects to `jwanlhdmhgmbybcdhvkx.supabase.co` (journey logging).
- CORS for both site domains (www and bare) is already allowed server-side.

## What was verified (20 Aug)

Quick variant tested live end-to-end in a browser: box → session minted +
`name_submitted` logged → hand-off to `/uk-trademark-quick-search#classes`
with the session adopted (no duplicate session) and the name pre-filled.
The free variant is the same code path with `screen=marks` — the box's
original, already-proven behaviour.
