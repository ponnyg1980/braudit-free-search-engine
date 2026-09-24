// Run:  deno run test_order_lines.ts
//   or: copy order_lines.ts, billing.gen.ts and this file to a folder with a
//       {"type":"module"} package.json, then: node --experimental-strip-types test_order_lines.ts
import { buildXeroLines, buildZohoOrder, classify, isWorldwide, pricePoint,
  type LineMeta, type Tier, type XeroLineOut } from "./order_lines.ts";

let fails = 0;
const eq = (name: string, got: unknown, want: unknown) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fails++;
  console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : `\n   got  ${JSON.stringify(got)}\n   want ${JSON.stringify(want)}`));
};
const rows = (b: { items: XeroLineOut[] }) => b.items.map((i) => [i.Description, i.UnitAmount]);
const net = (b: { items: XeroLineOut[] }) => Math.round(b.items.reduce((a, i) => a + i.UnitAmount, 0) * 100) / 100;
// Echo what we sent as if Xero returned it (20% VAT unless exempt).
const echo = (b: { items: XeroLineOut[]; meta: LineMeta[] }, exempt: boolean, tier: Tier, web: boolean) => {
  const li = b.items.map((s, i) => ({ LineItemID: "L" + i, Description: s.Description, Quantity: s.Quantity,
    LineAmount: s.UnitAmount * s.Quantity, TaxAmount: exempt ? 0 : Math.round(s.UnitAmount * s.Quantity * 20) / 100,
    AccountCode: s.AccountCode, TaxType: s.TaxType }));
  const sub = li.reduce((a, l) => a + l.LineAmount, 0), tax = li.reduce((a, l) => a + l.TaxAmount, 0);
  return buildZohoOrder({ invoice: { InvoiceID: "X", InvoiceNumber: "INV-T", SubTotal: sub, TotalTax: tax,
    Total: Math.round((sub + tax) * 100) / 100, LineItems: li }, meta: b.meta, sent: b.items, vatExempt: exempt,
    paid: true, paidDate: "2026-09-24", today: "2026-09-24", stripeId: "", syncNote: "", nowIso: "x", tier, webException: web });
};

eq("word", classify("Name search — ACME", false).desc, 'UK Clearance Audit – Word mark "ACME"');
eq("hyphen name", classify("Name — Coca-Cola", false).desc, 'UK Clearance Audit – Word mark "Coca-Cola"');
eq("ww", [isWorldwide(["GB"]), isWorldwide(["GB", "EU"]), isWorldwide([])], [false, true, false]);
eq("bands ww", [199, 169, 149, 99].map((v) => pricePoint("AUDIT_WORLDWIDE", v)),
   ["RRP", "Discounted", "Baseline", "Below Baseline"]);

// 1. Exception 1: direct web, name + logo + tagline, Worldwide, VAT. RRP 3x199,
//    ONE promotion to £99, consultation included.
const w = buildXeroLines({ lines: [{ l: "Name search — ACME", p: 19900 }, { l: "Logo search", p: 19900 },
  { l: "Tagline — Go", p: 19900 }], marks: 3, vatExempt: false, worldwide: true, tier: "Baseline",
  webException: true, consult: true });
eq("web rows", rows(w), [
  ['Worldwide Clearance Audit – Word mark "ACME"', 199], ["Worldwide Clearance Audit – Logo", 199],
  ['Worldwide Clearance Audit – Tagline "Go"', 199], ["Clearance Audit Promotion applied", -498],
  ["Clearance Consultation", 149], ["Clearance Consultation – included", -149]]);
eq("web net", net(w), 99);
const wz = echo(w, false, "Baseline", true);
eq("web price points", wz.items.map((i) => i.Price_Point ?? null),
   ["Baseline", "Baseline", "Baseline", null, "Included", null]);
eq("web deal money", wz.dealMoney, { RRP_Total_Fee_Amount: 746, Collecting_Fee_Invoiced: 99, Gross_Profit: 99 });
eq("web totals tie", [wz.order.Total, wz.warnings], [118.8, []]);

// 2. Introduced client on Discounted (UK): each element at £119.
const d = buildXeroLines({ lines: [{ l: "Name search — ACME", p: 11900 }, { l: "Logo search", p: 11900 }],
  marks: 2, vatExempt: false, worldwide: false, tier: "Discounted", webException: false, consult: true });
eq("introduced rows", rows(d), [
  ['UK Clearance Audit – Word mark "ACME"', 149], ["UK Clearance Audit – Logo", 149],
  ["Clearance Audit – discount applied", -60], ["Clearance Consultation", 149], ["Clearance Consultation – included", -149]]);
const dz = echo(d, false, "Discounted", false);
eq("introduced pp", dz.items.map((i) => i.Price_Point ?? null), ["Discounted", "Discounted", null, "Included", null]);
eq("introduced money", dz.dealMoney, { RRP_Total_Fee_Amount: 447, Collecting_Fee_Invoiced: 238, Gross_Profit: 202.3 });

// 3. RRP-tier client: no discount line at all on the audit.
const r = buildXeroLines({ lines: [{ l: "Name — X", p: 14900 }], marks: 1, vatExempt: true, worldwide: false,
  tier: "RRP", webException: false, consult: false });
eq("rrp rows", rows(r), [['UK Clearance Audit – Word mark "X"', 149]]);
eq("rrp money", echo(r, true, "RRP", false).dealMoney, { RRP_Total_Fee_Amount: 149, Collecting_Fee_Invoiced: 149, Gross_Profit: 104.3 });

// 4. Staff, direct client dropped to Baseline, Worldwide, overseas; consultation
//    charged at £99 (discount removed partly); one line raised above RRP.
const s = buildXeroLines({ lines: [{ l: "Name — Hailaflo", p: 14900 }, { l: "Logo — Hailaflo logo", p: 25000 },
  { l: "Audit Consultation", p: 9900 }], marks: 2, vatExempt: true, worldwide: true, tier: "Baseline", webException: false });
eq("staff rows", rows(s), [
  ['Worldwide Clearance Audit – Word mark "Hailaflo"', 199], ['Worldwide Clearance Audit – Logo "Hailaflo logo"', 250],
  ["Clearance Audit – discount applied", -50], ["Clearance Consultation", 149], ["Clearance Consultation – discount applied", -50]]);
eq("staff net", net(s), 149 + 250 + 99);
const sz = echo(s, true, "Baseline", false);
eq("staff pp", sz.items.map((i) => i.Price_Point ?? null), ["Baseline", "Baseline", null, "Baseline", null]);
eq("staff codes", sz.items.map((i) => [i.product_code, i.Xero_Tax_Type]).slice(0, 4), [
  ["UKTM-AUD-WW-INTL", "NONE"], ["UKTM-AUD-WW-INTL", "NONE"], ["UKTM-AUD-WW-INTL", "NONE"], ["UKTM-CONS-INTL", "NONE"]]);

// 5. Below the floor (Super Admin override): Discounted tier, element at £80.
const b = buildXeroLines({ lines: [{ l: "Name — Y", p: 8000 }, { l: "Audit Consultation — discounted to £0", p: 0 }],
  marks: 1, vatExempt: false, worldwide: false, tier: "Discounted", webException: false });
eq("below floor pp", echo(b, false, "Discounted", false).items.map((i) => i.Price_Point ?? null),
   ["Below Baseline", null, "Included", null]);

// 6. Legacy event: no lines, no tier -> the old single line, web rules.
const l = buildXeroLines({ lines: [], marks: 2, vatExempt: false, worldwide: false, tier: "Baseline",
  webException: true, consult: true });
eq("legacy rows", rows(l), [["UK Clearance Audit × 2 marks – promotion applied", 99],
  ["Clearance Consultation", 149], ["Clearance Consultation – included", -149]]);

console.log(fails ? `${fails} FAILED` : "ALL PASS");
if (fails) throw new Error("tests failed");
