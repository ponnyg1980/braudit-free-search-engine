// Invoice lines -> Zoho Orders + Deal_Items (handoff 23 Sep 2026, Jonathan).
//
// Pure functions only: no network, no env. index.ts /xero/process calls
// `buildXeroLines` to shape what it SENDS to Xero, and `buildZohoOrder` to
// shape what Xero RETURNED into the payload for the Deluge stage
// `order_lines`. Kept apart from index.ts so the arithmetic can be tested on
// its own (test_order_lines.ts).
//
// Prices come from billing.gen.ts (generated from the hub catalogue), never
// typed here. Account codes and tax types likewise (tmh_billing.py).

import { BILLING_CODES, CATALOGUE } from "./billing.gen.ts";

export type Sku = keyof typeof CATALOGUE;
export type LineType = "TMH Fee" | "Discount";

// Orders.Status values, in one place. Zoho stores these as shown.
export const ORDER_STATUS = { invoiced: "Invoiced", paid: "Paid" } as const;

export interface InLine { l: string; p: number }          // p = pence, net
export type Tier = "RRP" | "Discounted" | "Baseline";
export const TIERS: readonly Tier[] = ["RRP", "Discounted", "Baseline"];
/** Introducer commission by the order's tier (handoff §9, until the ledger). */
export const COMMISSION_RATE: Record<Tier, number> = { RRP: 0.30, Discounted: 0.15, Baseline: 0 };
/** Exception 1 (§9): a direct web order's audits total this, whatever the count. */
export const WEB_ORDER_TOTAL = CATALOGUE.AUDIT_UK.baseline;

export interface LineMeta {
  sku: Sku; lineType: LineType;
  /** fee lines: what the client is actually charged per unit, net */
  charged?: number;
  /** consultation given free with the audit -> Price_Point "Included" */
  included?: boolean;
}

/** The catalogue price of a product at a tier. */
export function tierPrice(sku: Sku, tier: Tier): number {
  const c = CATALOGUE[sku];
  return tier === "RRP" ? c.rrp : tier === "Discounted" ? c.discounted : c.baseline;
}

export function asTier(v: unknown): Tier | null {
  const s = String(v ?? "");
  return (TIERS as readonly string[]).includes(s) ? s as Tier : null;
}
export interface XeroLineOut {
  Description: string; Quantity: number; UnitAmount: number;
  AccountCode: string; TaxType: string; Tracking?: unknown;
}

export function accountFor(vatExempt: boolean): { code: string; tax: string } {
  return vatExempt
    ? { code: BILLING_CODES.nonuk, tax: BILLING_CODES.tax_nonuk }
    : { code: BILLING_CODES.uk, tax: BILLING_CODES.tax_uk };
}

export function isForbidden(code: string): boolean {
  return (BILLING_CODES.forbidden as readonly string[]).includes(String(code));
}

/** Any territory beyond the UK makes it a Worldwide Clearance Audit. */
export function isWorldwide(codes: unknown[]): boolean {
  return codes.map((c) => String(c ?? "").trim().toUpperCase())
    .some((c) => c !== "" && c !== "GB" && c !== "UK" && c !== "UNITED KINGDOM");
}

const q = (s: string) => `"${s.replace(/"/g, "'").trim()}"`;

/** One carried label ('Name search — ACME', 'Logo — x', 'Audit Consultation…')
 *  -> the product it is and the customer-facing Xero description. The
 *  carriers are unchanged (Stripe metadata, stored enquiries); only the text
 *  printed on the invoice is renamed Clearance Audit / Clearance Consultation. */
export function classify(label: string, worldwide: boolean): { sku: Sku; desc: string } {
  const raw = String(label ?? "").trim();
  if (/consultation/i.test(raw)) {
    const waived = /discounted to £0/i.test(raw);
    return { sku: "CONSULT_CLEARANCE",
             desc: "Clearance Consultation" + (waived ? " – discounted to £0" : "") };
  }
  const sku: Sku = worldwide ? "AUDIT_WORLDWIDE" : "AUDIT_UK";
  const head = CATALOGUE[sku].name;                       // 'UK Clearance Audit'
  const m = raw.match(/^([^—–-]+?)\s*[—–-]\s*(.+)$/);
  const kind = (m ? m[1] : raw).trim().toLowerCase();
  const text = m ? m[2].trim() : "";
  let what: string;
  if (kind.startsWith("logo")) {
    what = "Logo" + (/to follow/i.test(raw) ? " (to follow)" : "") + (text ? " " + q(text) : "");
  } else if (kind.startsWith("tagline")) what = "Tagline " + q(text);
  else if (kind.startsWith("product")) what = "Product name " + q(text);
  else if (kind.startsWith("name")) what = "Word mark " + q(text);
  else if (text) what = "Mark " + q(text);
  else what = "";
  return { sku, desc: what ? `${head} – ${what}` : head };
}

/** What we SEND to Xero, plus a parallel array saying what each line is.
 *
 *  FINAL PRICING RULES (handoff §9, Jonathan 24 Sep 2026):
 *  - Invoices ALWAYS show RRP: every fee line at its product's RRP, then a
 *    separate discount line down to what is charged. The client sees the
 *    saving. (A line staff priced ABOVE RRP is shown at that price.)
 *  - Exception 1, direct web order: one promotion line takes the AUDIT total
 *    to WEB_ORDER_TOTAL for the whole order, however many elements.
 *  - Otherwise each element is charged at its own `p` (the tier price, or
 *    what staff set), and one discount line covers the audit lines.
 *  - The consultation is always its own pair: £RRP, then "– included" at
 *    minus RRP. If staff charge it, it is a normal fee line (+ a discount line
 *    if charged below RRP).
 *  - Discount / included lines carry the same account and tax type as the
 *    lines they reduce. */
export function buildXeroLines(opts: {
  lines: InLine[]; marks: number; vatExempt: boolean; worldwide: boolean;
  tier: Tier; webException: boolean;
  /** Client wizard: add the included consultation if no line carries one. */
  consult?: boolean;
  /** Legacy events only (no line list / no tier): old promo in pence. */
  discountPence?: number;
}): { items: XeroLineOut[]; meta: LineMeta[] } {
  const { code, tax } = accountFor(opts.vatExempt);
  const items: XeroLineOut[] = [];
  const meta: LineMeta[] = [];
  const push = (Description: string, UnitAmount: number, m: LineMeta, Quantity = 1) => {
    items.push({ Description, Quantity, UnitAmount: Math.round(UnitAmount * 100) / 100,
                 AccountCode: code, TaxType: tax });
    meta.push(m);
  };
  const consultRrp = CATALOGUE.CONSULT_CLEARANCE.rrp;
  const consultLines = (charged: number) => {
    if (charged <= 0) {
      push("Clearance Consultation", consultRrp,
           { sku: "CONSULT_CLEARANCE", lineType: "TMH Fee", charged: 0, included: true });
      push("Clearance Consultation – included", -consultRrp,
           { sku: "CONSULT_CLEARANCE", lineType: "Discount" });
      return;
    }
    const unit = Math.max(consultRrp, charged);
    push("Clearance Consultation", unit, { sku: "CONSULT_CLEARANCE", lineType: "TMH Fee", charged });
    if (unit - charged > 0.004) {
      push("Clearance Consultation – discount applied", -(unit - charged),
           { sku: "CONSULT_CLEARANCE", lineType: "Discount" });
    }
  };

  if (!opts.lines.length) {
    // Legacy event with no line list: the old single £99-per-mark line.
    const sku: Sku = opts.worldwide ? "AUDIT_WORLDWIDE" : "AUDIT_UK";
    const marks = Math.max(1, opts.marks);
    push(CATALOGUE[sku].name + (marks > 1 ? ` × ${marks} marks` : "") + " – promotion applied"
           + (opts.vatExempt ? " (VAT not applicable, outside the UK)" : ""),
         WEB_ORDER_TOTAL, { sku, lineType: "TMH Fee", charged: WEB_ORDER_TOTAL }, marks);
    if (opts.consult) consultLines(0);
    return { items, meta };
  }

  let consultCharged: number | null = null;
  let audUnits = 0, audCharged = 0;
  let audSku: Sku | null = null;
  for (const x of opts.lines) {
    const c = classify(x.l, opts.worldwide);
    const charged = x.p / 100;
    if (c.sku === "CONSULT_CLEARANCE") { consultCharged = charged; continue; }
    const unit = Math.max(CATALOGUE[c.sku].rrp, charged);
    push(c.desc, unit, { sku: c.sku, lineType: "TMH Fee", charged });
    audUnits += unit; audCharged += charged; audSku = audSku ?? c.sku;
  }
  if (audSku) {
    const target = opts.webException ? WEB_ORDER_TOTAL : audCharged;
    const off = audUnits - target;
    if (off > 0.004) {
      push(opts.webException ? "Clearance Audit Promotion applied"
                             : "Clearance Audit – discount applied",
           -off, { sku: audSku, lineType: "Discount" });
    }
    if (opts.webException) {
      // Spread the £99 over the elements so Price_Point sees the real charge.
      const n = meta.filter((m) => m.lineType === "TMH Fee" && m.sku === audSku).length || 1;
      for (const m of meta) if (m.lineType === "TMH Fee" && m.sku === audSku) m.charged = target / n;
    }
  }
  if (consultCharged !== null) consultLines(consultCharged);
  else if (opts.consult) consultLines(0);
  return { items, meta };
}

/** Price_Point (§9): the order's tier on every fee line; "Included" on the
 *  free consultation; "Below Baseline" only when a line is charged under the
 *  order's floor (Super Admin override). The web £99 is sanctioned: Baseline. */
export function pricePointFor(m: LineMeta, tier: Tier, webException: boolean): string | null {
  if (m.lineType !== "TMH Fee") return null;
  if (m.included) return "Included";
  if (webException) return "Baseline";
  const floor = tierPrice(m.sku, tier);
  if ((m.charged ?? floor) < floor - 0.005) return "Below Baseline";
  return tier;
}

/** Band a net per-unit price against that product's own three prices. */
export function pricePoint(sku: Sku, netUnit: number): string {
  const c = CATALOGUE[sku];
  const e = 0.005;
  if (netUnit >= c.rrp - e) return "RRP";
  if (netUnit >= c.discounted - e) return "Discounted";
  if (netUnit >= c.baseline - e) return "Baseline";
  return "Below Baseline";
}

export interface XeroInvoiceIn {
  InvoiceID: string; InvoiceNumber?: string; Date?: string; DueDate?: string;
  SubTotal?: number; TotalTax?: number; Total?: number; TotalDiscount?: number;
  LineItems?: { LineItemID?: string; Description?: string; Quantity?: number;
    UnitAmount?: number; LineAmount?: number; TaxAmount?: number;
    AccountCode?: string; TaxType?: string }[];
}

const r2 = (n: number) => Math.round(n * 100) / 100;

/** Xero date -> YYYY-MM-DD. Xero answers '/Date(1727136000000+0000)/'. */
export function xeroDate(v: unknown, fallback: string): string {
  const s = String(v ?? "");
  const m = s.match(/\/Date\((\d+)/);
  if (m) return new Date(Number(m[1])).toISOString().slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  return fallback;
}

/** What Xero RETURNED -> the Deluge `order_lines` payload. Money values are
 *  Xero's, never recomputed from what was sent; `meta` says what each line
 *  IS. Also returns the three Deal money figures (§9). */
export function buildZohoOrder(opts: {
  invoice: XeroInvoiceIn; meta: LineMeta[]; sent: XeroLineOut[];
  vatExempt: boolean; paid: boolean; paidDate: string; today: string;
  stripeId: string; syncNote: string; nowIso: string;
  tier: Tier; webException: boolean;
}): { order: Record<string, unknown>; items: Record<string, unknown>[];
      dealMoney: Record<string, number>; warnings: string[] } {
  const inv = opts.invoice;
  const warnings: string[] = [];
  const xl = inv.LineItems ?? [];
  if (xl.length !== opts.meta.length) {
    warnings.push(`Xero returned ${xl.length} lines for ${opts.meta.length} sent`);
  }
  // Pair returned lines with what we sent: by position, confirmed by text.
  const pairs = xl.map((l, i) => {
    let k = i;
    if (opts.sent[i]?.Description !== l.Description) {
      const j = opts.sent.findIndex((s) => s.Description === l.Description);
      if (j >= 0) k = j;
    }
    return { l, m: opts.meta[k] ?? opts.meta[0] };
  });
  const sum = (t: LineType) => pairs.filter((p) => p.m.lineType === t)
    .reduce((a, p) => a + Number(p.l.LineAmount ?? 0), 0);
  const feeSum = sum("TMH Fee");
  const discSum = sum("Discount");                                     // negative
  let rrpTotal = 0;
  const items = pairs.map(({ l, m }) => {
    const amount = r2(Number(l.LineAmount ?? 0));
    const tax = r2(Number(l.TaxAmount ?? 0));
    const qty = Number(l.Quantity ?? 1) || 1;
    const listPrice = m.lineType === "Discount" ? 0 : CATALOGUE[m.sku].rrp;
    if (m.lineType === "TMH Fee") rrpTotal += listPrice * qty;
    const rec: Record<string, unknown> = {
      Xero_Line_ID: l.LineItemID ?? "",
      product_code: opts.vatExempt ? CATALOGUE[m.sku].product_code_intl
                                   : CATALOGUE[m.sku].product_code_dom,
      Description: String(l.Description ?? "").slice(0, 2000),
      Quantity: m.lineType === "Discount" ? 1 : qty,
      List_Price: listPrice,
      Amount: amount, Discount: 0, Tax: tax, Total: r2(amount + tax),
      Line_Type: m.lineType,
      Xero_Account_Code: String(l.AccountCode ?? ""),
      Xero_Tax_Type: String(l.TaxType ?? ""),
      Currency: "GBP",
    };
    const pp = pricePointFor(m, opts.tier, opts.webException);
    if (pp) rec.Price_Point = pp;
    return rec;
  });
  const status = opts.paid ? ORDER_STATUS.paid : ORDER_STATUS.invoiced;
  const invDate = xeroDate(inv.Date, opts.today);
  const order: Record<string, unknown> = {
    Name: inv.InvoiceNumber ?? inv.InvoiceID,
    Source: "Search Journey", Order_Type: "One-off", Status: status,
    Currency: "GBP",
    Xero_Invoice_ID: inv.InvoiceID, Invoice_Number: inv.InvoiceNumber ?? "",
    Order_Date: invDate, Invoice_Date: invDate,
    Invoice_Due_Date: xeroDate(inv.DueDate, invDate),
    Subtotal: r2(Number(inv.SubTotal ?? 0)),
    Discount: r2(Math.abs(discSum) + Number(inv.TotalDiscount ?? 0)),
    VAT: r2(Number(inv.TotalTax ?? 0)),
    Total: r2(Number(inv.Total ?? 0)),
    Service_Fees: r2(feeSum),
    Net_Service_Fees: r2(Number(inv.SubTotal ?? 0)),
    Official_Fees: 0,
    Xero_Sync_Time: opts.nowIso,
    Xero_Sync_Details: [opts.syncNote, ...warnings].filter(Boolean).join(" | ").slice(0, 2000),
  };
  if (opts.paid && opts.paidDate) order.Paid_Date = opts.paidDate;
  if (opts.stripeId) order.Stripe_Invoice_ID = opts.stripeId;
  const sumTotal = r2(items.reduce((a, i) => a + Number(i.Total), 0));
  if (Math.abs(sumTotal - Number(order.Total)) > 0.011) {
    warnings.push(`line totals ${sumTotal} != invoice total ${order.Total}`);
    order.Xero_Sync_Details = [order.Xero_Sync_Details, warnings.at(-1)].filter(Boolean).join(" | ");
  }
  // The three Deal money figures (§9). Official fees, rep fees and FX margin
  // never enter them; an audit order has none. Commission by the order's tier
  // until the commission ledger exists.
  const collecting = r2(feeSum + discSum);
  const dealMoney = {
    RRP_Total_Fee_Amount: r2(rrpTotal),
    Collecting_Fee_Invoiced: collecting,
    Gross_Profit: r2(collecting * (1 - COMMISSION_RATE[opts.webException ? "Baseline" : opts.tier])),
  };
  return { order, items, dealMoney, warnings };
}
