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
export interface LineMeta { sku: Sku; lineType: LineType }
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

/** What we SEND to Xero, plus a parallel array saying what each line is. */
export function buildXeroLines(opts: {
  lines: InLine[]; discountPence: number; marks: number;
  vatExempt: boolean; worldwide: boolean;
  /** Client wizard: consultation booked. Staff orders carry it as a line. */
  consult?: boolean;
}): { items: XeroLineOut[]; meta: LineMeta[] } {
  const { code, tax } = accountFor(opts.vatExempt);
  const items: XeroLineOut[] = [];
  const meta: LineMeta[] = [];
  // CONSULTATION (Jonathan, 24 Sep 2026): "Included at RRP, discounted to
  // £0." Always its own line at the catalogue RRP, followed by its own
  // negative line taking it to nil -- so the client sees the value, the
  // net is unchanged, and Zoho carries it as a Product row.
  const consultPair = () => {
    const c = CATALOGUE.CONSULT_CLEARANCE;
    items.push({ Description: "Clearance Consultation", Quantity: 1,
                 UnitAmount: c.rrp, AccountCode: code, TaxType: tax });
    meta.push({ sku: "CONSULT_CLEARANCE", lineType: "TMH Fee" });
    items.push({ Description: "Clearance Consultation – included, discounted to £0",
                 Quantity: 1, UnitAmount: -c.rrp, AccountCode: code, TaxType: tax });
    meta.push({ sku: "CONSULT_CLEARANCE", lineType: "Discount" });
  };
  if (opts.lines.length) {
    // Each discount sits under the line it discounts: audit lines, then the
    // audit promotion, then the consultation and its own nil-ing line.
    let hadConsult = false;
    let waivedConsult = false;
    for (const x of opts.lines) {
      const c = classify(x.l, opts.worldwide);
      if (c.sku === "CONSULT_CLEARANCE") {
        hadConsult = true;
        if (x.p === 0) { waivedConsult = true; continue; }
      }
      items.push({ Description: c.desc, Quantity: 1, UnitAmount: x.p / 100,
                   AccountCode: code, TaxType: tax });
      meta.push({ sku: c.sku, lineType: "TMH Fee" });
    }
    if (opts.discountPence > 0) {
      // Same Product as the line it discounts: the first audit line.
      const first = meta.find((m) => m.sku !== "CONSULT_CLEARANCE") ?? meta[0];
      items.push({ Description: "Clearance Audit Promotion applied", Quantity: 1,
                   UnitAmount: -opts.discountPence / 100, AccountCode: code, TaxType: tax });
      meta.push({ sku: first.sku, lineType: "Discount" });
    }
    if (waivedConsult || (opts.consult && !hadConsult)) consultPair();
  } else {
    // Legacy event with no line list: the old single £99-per-mark line.
    const sku: Sku = opts.worldwide ? "AUDIT_WORLDWIDE" : "AUDIT_UK";
    const marks = Math.max(1, opts.marks);
    items.push({
      Description: CATALOGUE[sku].name + (marks > 1 ? ` × ${marks} marks` : "")
        + " – promotion applied"
        + (opts.vatExempt ? " (VAT not applicable, outside the UK)" : ""),
      Quantity: marks, UnitAmount: CATALOGUE.AUDIT_UK.baseline,
      AccountCode: code, TaxType: tax });
    meta.push({ sku, lineType: "TMH Fee" });
    if (opts.consult) consultPair();
  }
  return { items, meta };
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

/** What Xero RETURNED -> the Deluge `order_lines` payload. Values are Xero's,
 *  never recomputed from what was sent; `meta` only says what each line IS. */
export function buildZohoOrder(opts: {
  invoice: XeroInvoiceIn; meta: LineMeta[]; sent: XeroLineOut[];
  vatExempt: boolean; paid: boolean; paidDate: string; today: string;
  stripeId: string; syncNote: string; nowIso: string;
}): { order: Record<string, unknown>; items: Record<string, unknown>[]; warnings: string[] } {
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
  const fees = pairs.filter((p) => p.m.lineType === "TMH Fee");
  const feeSum = fees.reduce((a, p) => a + Number(p.l.LineAmount ?? 0), 0);
  const promo = pairs.filter((p) => p.m.lineType === "Discount")
    .reduce((a, p) => a + Number(p.l.LineAmount ?? 0), 0);          // negative
  // Discounts are shared only among fee lines of the SAME product: the audit
  // promotion never lowers the consultation's price point, nor vice versa.
  const bySku = (t: LineType, sku: Sku) => pairs
    .filter((p) => p.m.lineType === t && p.m.sku === sku)
    .reduce((a, p) => a + Number(p.l.LineAmount ?? 0), 0);
  const items = pairs.map(({ l, m }) => {
    const amount = r2(Number(l.LineAmount ?? 0));
    const tax = r2(Number(l.TaxAmount ?? 0));
    const qty = Number(l.Quantity ?? 1) || 1;
    const rec: Record<string, unknown> = {
      Xero_Line_ID: l.LineItemID ?? "",
      product_code: opts.vatExempt ? CATALOGUE[m.sku].product_code_intl
                                   : CATALOGUE[m.sku].product_code_dom,
      Description: String(l.Description ?? "").slice(0, 2000),
      Quantity: m.lineType === "Discount" ? 1 : qty,
      List_Price: m.lineType === "Discount" ? 0 : CATALOGUE[m.sku].rrp,
      Amount: amount, Discount: 0, Tax: tax, Total: r2(amount + tax),
      Line_Type: m.lineType,
      Xero_Account_Code: String(l.AccountCode ?? ""),
      Xero_Tax_Type: String(l.TaxType ?? ""),
      Currency: "GBP",
    };
    if (m.lineType === "TMH Fee") {
      // The promotion is one negative line across all fee lines: share it
      // in proportion to each line's value, then price per unit.
      const skuFees = bySku("TMH Fee", m.sku);
      const share = skuFees > 0 ? bySku("Discount", m.sku) * (amount / skuFees) : 0;
      rec.Price_Point = pricePoint(m.sku, (amount + share) / qty);
    }
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
    Discount: r2(Math.abs(promo) + Number(inv.TotalDiscount ?? 0)),
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
  return { order, items, warnings };
}
