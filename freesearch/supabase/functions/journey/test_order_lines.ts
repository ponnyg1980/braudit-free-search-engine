// Run:  deno run test_order_lines.ts
//   or: node --experimental-strip-types test_order_lines.ts  (copies needed; see README note)
import { buildXeroLines, buildZohoOrder, classify, isWorldwide, pricePoint } from "./order_lines.ts";

let fails = 0;
const eq = (name: string, got: unknown, want: unknown) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : `\n   got  ${JSON.stringify(got)}\n   want ${JSON.stringify(want)}`));
};

eq("word", classify("Name search — ACME", false).desc, 'UK Clearance Audit – Word mark "ACME"');
eq("hyphen name", classify("Name — Coca-Cola", false).desc, 'UK Clearance Audit – Word mark "Coca-Cola"');
eq("logo", classify("Logo search (logo to follow)", false).desc, "UK Clearance Audit – Logo (to follow)");
eq("tagline ww", classify("Tagline — Just do", true), { sku: "AUDIT_WORLDWIDE", desc: 'Worldwide Clearance Audit – Tagline "Just do"' });
eq("consult waived", classify("Audit Consultation — discounted to £0", false), { sku: "CONSULT_CLEARANCE", desc: "Clearance Consultation – discounted to £0" });
eq("ww", [isWorldwide(["GB"]), isWorldwide(["GB", "EU"]), isWorldwide([])], [false, true, false]);
eq("bands uk", ["149", "130", "119", "99", "33"].map((v) => pricePoint("AUDIT_UK", Number(v))),
   ["RRP", "Discounted", "Discounted", "Baseline", "Below Baseline"]);
eq("bands ww", [199, 169, 149, 99].map((v) => pricePoint("AUDIT_WORLDWIDE", v)),
   ["RRP", "Discounted", "Baseline", "Below Baseline"]);

// Client wizard, one name, UK, VAT: £149 - £50 promo = £99 net, £118.80 gross.
const a = buildXeroLines({ lines: [{ l: "Name search — ACME", p: 14900 }], discountPence: 5000,
  marks: 1, vatExempt: false, worldwide: false });
eq("client lines", a.items.map((i) => [i.Description, i.UnitAmount, i.AccountCode, i.TaxType]), [
  ['UK Clearance Audit – Word mark "ACME"', 149, "227", "OUTPUT2"],
  ["Clearance Audit Promotion applied", -50, "227", "OUTPUT2"]]);
const xa = { InvoiceID: "X1", InvoiceNumber: "INV-1", Date: "/Date(1790208000000+0000)/",
  SubTotal: 99, TotalTax: 19.8, Total: 118.8, LineItems: [
    { LineItemID: "L1", Description: a.items[0].Description, Quantity: 1, LineAmount: 149, TaxAmount: 29.8, AccountCode: "227", TaxType: "OUTPUT2" },
    { LineItemID: "L2", Description: a.items[1].Description, Quantity: 1, LineAmount: -50, TaxAmount: -10, AccountCode: "227", TaxType: "OUTPUT2" }] };
const za = buildZohoOrder({ invoice: xa, meta: a.meta, sent: a.items, vatExempt: false, paid: true,
  paidDate: "2026-09-24", today: "2026-09-24", stripeId: "pi_1", syncNote: "ok", nowIso: "2026-09-24T10:00:00+00:00" });
eq("client items", za.items.map((i) => [i.product_code, i.List_Price, i.Amount, i.Tax, i.Total, i.Line_Type, i.Price_Point ?? null]), [
  ["UKTM-AUD-UK-DOM", 149, 149, 29.8, 178.8, "TMH Fee", "Baseline"],
  ["UKTM-AUD-UK-DOM", 0, -50, -10, -60, "Discount", null]]);
eq("client order", [za.order.Status, za.order.Subtotal, za.order.Discount, za.order.VAT, za.order.Total, za.order.Service_Fees, za.order.Invoice_Date, za.warnings], ["Paid", 99, 50, 19.8, 118.8, 149, "2026-09-24", []]);

// Staff, overseas, worldwide, name at £149 + logo at £0 + consultation waived.
const b = buildXeroLines({ lines: [{ l: "Name — Hailaflo", p: 14900 }, { l: "Logo — Hailaflo logo", p: 0 },
  { l: "Audit Consultation — discounted to £0", p: 0 }], discountPence: 0, marks: 3, vatExempt: true, worldwide: true });
const xb = { InvoiceID: "X2", InvoiceNumber: "INV-2", SubTotal: 149, TotalTax: 0, Total: 149,
  LineItems: b.items.map((s, i) => ({ LineItemID: "M" + i, Description: s.Description, Quantity: 1, LineAmount: s.UnitAmount, TaxAmount: 0, AccountCode: s.AccountCode, TaxType: s.TaxType })) };
const zb = buildZohoOrder({ invoice: xb, meta: b.meta, sent: b.items, vatExempt: true, paid: false,
  paidDate: "", today: "2026-09-24", stripeId: "", syncNote: "", nowIso: "2026-09-24T10:00:00+00:00" });
eq("staff consult pair", b.items.slice(2).map((i) => [i.Description, i.UnitAmount]), [
  ["Clearance Consultation", 149], ["Clearance Consultation – included, discounted to £0", -149]]);
eq("staff items", zb.items.map((i) => [i.product_code, i.List_Price, i.Price_Point ?? null, i.Xero_Account_Code, i.Xero_Tax_Type]), [
  ["UKTM-AUD-WW-INTL", 199, "Baseline", "247", "NONE"],
  ["UKTM-AUD-WW-INTL", 199, "Below Baseline", "247", "NONE"],
  ["UKTM-CONS-INTL", 149, "Below Baseline", "247", "NONE"],
  ["UKTM-CONS-INTL", 0, null, "247", "NONE"]]);
eq("staff order", [zb.order.Status, zb.order.Total, zb.order.Paid_Date ?? null], ["Invoiced", 149, null]);

// Client wizard with consultation booked: name £149, promo -£50, consult pair.
const c = buildXeroLines({ lines: [{ l: "Name search — ACME", p: 14900 }], discountPence: 5000,
  marks: 1, vatExempt: false, worldwide: false, consult: true });
eq("client consult lines", c.items.map((i) => [i.Description, i.UnitAmount]), [
  ['UK Clearance Audit – Word mark "ACME"', 149], ["Clearance Audit Promotion applied", -50],
  ["Clearance Consultation", 149], ["Clearance Consultation – included, discounted to £0", -149]]);
eq("client consult net", c.items.reduce((a, i) => a + i.UnitAmount, 0), 99);
const xc = { InvoiceID: "X3", SubTotal: 99, TotalTax: 19.8, Total: 118.8,
  LineItems: c.items.map((s, i) => ({ LineItemID: "C" + i, Description: s.Description, Quantity: 1,
    LineAmount: s.UnitAmount, TaxAmount: Math.round(s.UnitAmount * 20) / 100, AccountCode: s.AccountCode, TaxType: s.TaxType })) };
const zc = buildZohoOrder({ invoice: xc, meta: c.meta, sent: c.items, vatExempt: false, paid: true,
  paidDate: "2026-09-24", today: "2026-09-24", stripeId: "", syncNote: "", nowIso: "x" });
eq("client consult pp", zc.items.map((i) => [i.product_code, i.Line_Type, i.Price_Point ?? null]), [
  ["UKTM-AUD-UK-DOM", "TMH Fee", "Baseline"], ["UKTM-AUD-UK-DOM", "Discount", null],
  ["UKTM-CONS-DOM", "TMH Fee", "Below Baseline"], ["UKTM-CONS-DOM", "Discount", null]]);
eq("client consult total", [zc.order.Total, zc.warnings], [118.8, []]);

console.log(fails ? `${fails} FAILED` : "ALL PASS");
if (fails) throw new Error("tests failed");
