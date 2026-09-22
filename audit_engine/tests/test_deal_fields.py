"""Unit tests for the Zoho→engine adapter layer. Pure Python, no network.

    python3 -m unittest audit_engine.tests.test_deal_fields -v
"""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))          # temmy-access/

# deal_reader imports uk_monitor/zoho_client (needs secrets) and runner (needs
# tmh_scoring). Stub both so the assembly logic is testable on its own.
_stub = types.ModuleType("zoho_client")
_stub.call = lambda *a, **k: {"data": []}
_stub.update = lambda *a, **k: []
sys.modules["zoho_client"] = _stub

from audit_engine import deal_fields  # noqa: E402

HAILAFLO = {  # shape as returned by GET Deals/1964745000119447114 on 13 Sep 2026
    "id": "1964745000119447114",
    "Deal_Name": "UK, EU & US Audit - Hailaflo (word and logo)",
    "Search_Word_1": "Hailaflo", "Search_Operator_1": "Exact Match",
    "Search_Word_2": "Hailaflo", "Search_Operator_2": "Similar To",
    "Trademark_Jurisdictions": [], "Trademark_Search_Platforms": ["UK Office", "EU Office", "USA Office"],
    "Braudit_Search_Types": [], "Report_Type": "Combined", "Logo_Awaited": False,
    "Account_Name": {"name": "CHIA’s Essence", "id": "1964745000119447105"},
    "Classes_Terms": [{"Classes": ["5 - Preparations"], "Terms1": "Sanitary pants"},
                      {"Classes": ["25 - Clothing"], "Terms1": "Underwear"}],
    "Image_Mark_Information": [{"id": "1964745000119893233", "Vienna_Codes": [],
                                "Post_S_Img_Class_Div_Sub_Div": None, "Image_Nickname": None,
                                "Image_JPEG": [{"File_Name__s": "Hailaflo Logo.jpg", "Size__s": 25005,
                                                "id": "1964745000119893234",
                                                "File_Id__s": "jx5di553f3e50e679477dabde42beae3c08fd"}]}],
    "Excel_Report_Link": "https://thetrademarkhelpline.sharepoint.com/...",
}


class Keywords(unittest.TestCase):
    def test_deal_fields_are_the_live_source(self):
        words, src = deal_fields.keywords(HAILAFLO)
        self.assertEqual(src, "deal_fields")
        # 22 Sep 2026: "Exact", not "Exact Match", was the bug. Nothing else in
        # the system uses "Exact" — TMH_TO_SIGNA has no such key, so Signa
        # received no operator, and word_scoring tests `stype == "exact match"`,
        # so it fell through to contains+fuzzy. Declared one thing, scored
        # another. See decision I.
        self.assertEqual(words, [("Exact Match", "Hailaflo"), ("Similar To", "Hailaflo")])

    def test_gaps_in_slots_are_skipped(self):
        d = {"Search_Word_1": "", "Search_Word_3": "Blue Portal", "Search_Operator_3": "Contains"}
        self.assertEqual(deal_fields.keywords(d), ([("Contains", "Blue Portal")], "deal_fields"))

    def test_phonetic_operators_keep_their_label(self):
        """Sounds Like and Related Words are NOT flattened at the reader.

        They used to be, because Signa has no phonetic mode. But recall and
        assessment are different boundaries: criteria.TMH_TO_SIGNA degrades
        them for Signa, while word_scoring has a real phonetic method that
        needs the true label to use it. Degrade at the boundary that cannot
        cope, not at the reader (22 Sep 2026).
        """
        for op in ("Sounds Like", "Related Words"):
            d = {"Search_Word_1": "X", "Search_Operator_1": op}
            self.assertEqual(deal_fields.keywords(d)[0], [(op, "X")])

    def test_equals_is_exact(self):
        """The older picklist label for exact. It had no mapping, so it fell
        through the dict default to Similar To — silently turning an exact
        order into a broad one."""
        d = {"Search_Word_1": "X", "Search_Operator_1": "Equals"}
        self.assertEqual(deal_fields.keywords(d)[0], [("Exact Match", "X")])

    def test_unknown_operator_is_recorded_not_swallowed(self):
        d = {"Search_Word_1": "X", "Search_Operator_1": "Sideways"}
        self.assertEqual(deal_fields.keywords(d)[0], [("Similar To", "X")])
        notes = deal_fields.keyword_notes()
        self.assertTrue(any("Sideways" in n for n in notes), notes)

    def test_domain_operator_in_a_word_slot_is_skipped(self):
        """'Domain' is a real picklist value and not a word search. It used to
        default to Similar To and run as one."""
        d = {"Search_Word_1": "X", "Search_Operator_1": "Domain"}
        self.assertEqual(deal_fields.keywords(d)[0], [])
        self.assertTrue(any("Domain" in n for n in deal_fields.keyword_notes()))

    def test_declared_sources_are_choices_not_guesses(self):
        """Only an operator a person selected may govern the search. The legacy
        reader pairs a reconstructed phrase with a Similar To nobody chose."""
        self.assertIn("deal_fields", deal_fields.DECLARED_SOURCES)
        self.assertIn("search_records", deal_fields.DECLARED_SOURCES)
        self.assertNotIn("legacy", deal_fields.DECLARED_SOURCES)
        self.assertNotIn("none", deal_fields.DECLARED_SOURCES)

    def test_legacy_fallbacks(self):
        self.assertEqual(deal_fields.keywords({"TM_Text": " Acme "}), ([("Similar To", "Acme")], "legacy"))
        self.assertEqual(deal_fields.keywords({"Deal_Name": "UKTM Audit - Scent Grip (word)"}),
                         ([("Similar To", "Scent Grip")], "legacy"))
        self.assertEqual(deal_fields.keywords({}), ([], "none"))

    def test_search_records_take_precedence_when_present(self):
        orig = deal_fields._keywords_from_search_records
        deal_fields._keywords_from_search_records = lambda d: [("Exact", "From module")]
        try:
            self.assertEqual(deal_fields.keywords(HAILAFLO), ([("Exact", "From module")], "search_records"))
        finally:
            deal_fields._keywords_from_search_records = orig


class Vienna(unittest.TestCase):
    def test_tidy(self):
        self.assertEqual(deal_fields.tidy_vienna("26.04.01"), "26.4.1")
        self.assertEqual(deal_fields.tidy_vienna("26.4"), "26.4")
        self.assertEqual(deal_fields.tidy_vienna("abc"), "")

    def test_picklist_and_text_union_in_order(self):
        d = {"Image_Mark_Information": [
            {"Vienna_Codes": ["26.4", "29.1"], "Post_S_Img_Class_Div_Sub_Div": "26.04.01, 29.1.4; 26.4"},
            {"Vienna_Codes": ["3.1"], "Post_S_Img_Class_Div_Sub_Div": ""}]}
        self.assertEqual(deal_fields.vienna_codes(d), ["26.4", "29.1", "26.4.1", "29.1.4", "3.1"])

    def test_empty_subform(self):
        self.assertEqual(deal_fields.vienna_codes(HAILAFLO), [])
        self.assertEqual(deal_fields.vienna_codes({}), [])


class Logo(unittest.TestCase):
    def test_attached(self):
        lg = deal_fields.logo(HAILAFLO)
        self.assertEqual(lg["state"], "attached")
        self.assertEqual(lg["files"][0]["file_id"], "jx5di553f3e50e679477dabde42beae3c08fd")

    def test_awaited_only_when_no_file(self):
        self.assertEqual(deal_fields.logo({"Logo_Awaited": True})["state"], "awaited")
        self.assertEqual(deal_fields.logo({"Logo_Awaited": "true"})["state"], "awaited")
        self.assertEqual(deal_fields.logo({})["state"], "none")


class Jurisdictions(unittest.TestCase):
    def test_codes_win(self):
        self.assertEqual(deal_fields.jurisdictions({"Trademark_Jurisdictions": ["GB", "EM"],
                                                    "Trademark_Search_Platforms": ["USA Office"]}),
                         (["GB", "EM"], "Trademark_Jurisdictions"))

    def test_platforms_fallback_then_default(self):
        self.assertEqual(deal_fields.jurisdictions(HAILAFLO), (["GB", "EU", "US"], "Trademark_Search_Platforms"))
        self.assertEqual(deal_fields.jurisdictions({}), (["GB"], "default"))

    def test_display_names_resolve_to_codes(self):
        """17 Sep 2026: the API returns the picklist DISPLAY name ("United
        Kingdom") for anything picked in the CRM or written by name, and the
        code ("GB") for anything the forms wrote. Deal 1964745000121041019
        held the names, matched no office, and was searched on WIPO only."""
        self.assertEqual(
            deal_fields.jurisdictions({"Trademark_Jurisdictions": ["EU (EUIPO)", "United Kingdom", "United States"]}),
            (["EM", "GB", "US"], "Trademark_Jurisdictions"))
        # mixed vocabularies, duplicates and NONE collapse cleanly
        self.assertEqual(
            deal_fields.jurisdictions({"Trademark_Jurisdictions": ["United Kingdom", "GB", "NONE", "WIPO International", "Australia"]}),
            (["GB", "WO", "AU"], "Trademark_Jurisdictions"))
        # every name in the snapshot resolves to a code, never to itself
        names = deal_fields._jurisdiction_names()
        self.assertGreater(len(names), 200)
        self.assertTrue(all(len(v) in (2, 4) for v in names.values()), "codes are ISO-2 or NONE")
        # and the resolved codes are what sources.resolve routes on
        from audit_engine import sources
        srcs = sources.resolve(deal_fields.jurisdictions(
            {"Trademark_Jurisdictions": ["EU (EUIPO)", "United Kingdom", "United States"]})[0])
        self.assertEqual([s.label for s in srcs][:3], ["UKIPO (Temmy)", "EUIPO (Signa)", "USPTO (Signa)"])


class Channels(unittest.TestCase):
    def test_form_layers_win(self):
        d = {"Audit_Search_Layers": ["UKIPO (Temmy)", "EUIPO", "Companies House UK", "Domain Registers",
                                     "Instagram", "Amazon"],
             "Other_Search_Platforms": ["Google", "Facebook"]}
        ch = deal_fields.channels(d)
        self.assertEqual(ch["source"], "Audit_Search_Layers")
        self.assertEqual(ch["socials"], ["Instagram"])
        self.assertEqual(ch["marketplaces"], ["Amazon"])
        self.assertTrue(ch["include_companies"] and ch["include_domains"] and ch["include_serp"])

    def test_staff_picklists_fallback(self):
        ch = deal_fields.channels(HAILAFLO | {"Other_Search_Platforms": ["Google", "UK Companies House", "Linkedin"]})
        self.assertEqual(ch["source"], "Other_Search_Platforms")
        self.assertEqual(ch["socials"], ["LinkedIn"])
        self.assertIsNone(ch["marketplaces"])
        self.assertTrue(ch["include_companies"]); self.assertFalse(ch["include_domains"])

    def test_nothing_selected(self):
        ch = deal_fields.channels({})
        self.assertFalse(ch["include_serp"]); self.assertFalse(ch["socials_selected"])


class Exclusions(unittest.TestCase):
    ROWS = [{"id": "1", "Active": True, "Exclusion_Value": "zztestwordlogo.example", "Target_Type": "Domain", "Match_Mode": "Exact"},
            {"id": "2", "Active": False, "Exclusion_Value": "old.example", "Target_Type": "Domain"},
            {"id": "3", "Active": True, "Exclusion_Value": "UK00003000000", "Target_Type": "Trademark"},
            {"id": "4", "Active": "true", "Exclusion_Value": "", "Target_Type": "Domain"}]

    def test_only_active_rows_and_kinds(self):
        excl, items = deal_fields.exclusions_from_rows(self.ROWS)
        self.assertEqual([e["value"] for e in excl], ["zztestwordlogo.example", "UK00003000000"])
        self.assertEqual(excl[0]["kind"], "domain"); self.assertEqual(excl[1]["kind"], "trademark")
        # PORTFOLIO_KINDS was widened on 21 Sep 2026 (Jonathan: a client's own
        # trademark was excluded correctly but never appeared in Portfolio).
        # This assertion still encoded the old domain+handle-only promotion and
        # has been red since; it is not a regression from decision I.
        self.assertEqual(items, [
            ("domain", "zztestwordlogo.example", "Client_Search_Exclusions 1"),
            ("trademark", "UK00003000000", "Client_Search_Exclusions 3")])

    def test_empty(self):
        self.assertEqual(deal_fields.exclusions_from_rows([]), ([], []))


class ReaderAssembly(unittest.TestCase):
    """deal_reader on top of the adapter — with runner stubbed."""

    @classmethod
    def setUpClass(cls):
        import dataclasses
        runner = types.ModuleType("audit_engine.runner")

        @dataclasses.dataclass
        class AuditRequest:
            client_name: str
            mark_text: str
            classes: list = dataclasses.field(default_factory=list)
            jurisdictions: list = dataclasses.field(default_factory=list)
            tagline: str | None = None
            applicant: str | None = None
            goods_text: str = ""
            exclusions: list = dataclasses.field(default_factory=list)
            extra_criteria: list = dataclasses.field(default_factory=list)
            criteria_declared: bool = False
            deal_id: str = ""
            nature_of_business: str = ""
            search_date: object = None
            include_companies: bool = True
            include_domains: bool = True
            include_serp: bool = True
            logo_url: str | None = None
            vienna_codes: list = dataclasses.field(default_factory=list)
            logo_awaited: bool = False
            logo_ref: dict | None = None
            zoho_portfolio: list = dataclasses.field(default_factory=list)
            own_domains: list = dataclasses.field(default_factory=list)
            own_handles: list = dataclasses.field(default_factory=list)
            socials: list | None = None
            marketplaces: list | None = None
            # Kept in step with the real dataclass in runner.py. A stub that
            # drifts fails with "unexpected keyword argument", which says
            # nothing about the behaviour under test — so anything added to
            # AuditRequest must be mirrored here.
            deal_name: str = ""            # cover block, 16 Sep 2026
            contact_name: str = ""
            contact_email: str = ""
            deal_owner: str = ""
            sic_code: str = ""
            domain_criteria: list = dataclasses.field(default_factory=list)
            register_layers: list = dataclasses.field(default_factory=list)  # 17 Sep 2026
        runner.AuditRequest = AuditRequest
        sys.modules["audit_engine.runner"] = runner
        from audit_engine import deal_reader
        cls.dr = deal_reader

    def test_request_carries_image_inputs(self):
        d = dict(HAILAFLO)
        d["Image_Mark_Information"] = [dict(HAILAFLO["Image_Mark_Information"][0], Vienna_Codes=["26.4"])]
        req = self.dr.request_from_deal(d, with_exclusions=False)
        self.assertEqual(req.mark_text, "Hailaflo")
        # Word 1 now travels WITH its operator instead of being dropped on the
        # line that made its phrase the mark_text (decision I).
        self.assertEqual(req.extra_criteria,
                         [("Exact Match", "Hailaflo"), ("Similar To", "Hailaflo")])
        self.assertTrue(req.criteria_declared)
        self.assertEqual(req.classes, [5, 25])
        self.assertEqual(req.jurisdictions, ["GB", "EU", "US"])
        self.assertEqual(req.vienna_codes, ["26.4"])
        self.assertFalse(req.logo_awaited)
        self.assertEqual(req.logo_ref["name"], "Hailaflo Logo.jpg")

    def test_word_only_deal_never_runs_image_search(self):
        d = dict(HAILAFLO, Report_Type="Word Only", Braudit_Search_Types=["word"])
        d["Image_Mark_Information"] = []
        req = self.dr.request_from_deal(d, with_exclusions=False)
        self.assertEqual(req.vienna_codes, [])
        self.assertIsNone(req.logo_ref)

    def test_exclusions_fetched_through_zoho(self):
        calls = []
        self.dr.zoho.call = lambda m, p, *a, **k: (calls.append(p) or {"data": Exclusions.ROWS})
        req = self.dr.request_from_deal(HAILAFLO)
        self.assertIn("Client_Search_Exclusions/search", calls[0])
        self.assertIn("1964745000119447105", calls[0])
        self.assertEqual([e["value"] for e in req.exclusions], ["zztestwordlogo.example", "UK00003000000"])
        self.assertEqual(req.zoho_portfolio[0][1], "zztestwordlogo.example")

    def test_validate(self):
        self.assertEqual(self.dr.validate(HAILAFLO), [])
        bad = {"Deal_Name": "x", "Report_Type": "Combined"}
        probs = self.dr.validate(bad)
        self.assertTrue(any("search words" in p for p in probs))
        self.assertTrue(any("Nice classes" in p for p in probs))
        self.assertTrue(any("Account" in p for p in probs))
        self.assertTrue(any("image search requested" in p for p in probs))

    def test_summary_flags_legacy_report(self):
        s = self.dr.deal_summary(HAILAFLO)
        self.assertTrue(s["legacy_report"])
        self.assertEqual(s["keyword_source"], "deal_fields")
        self.assertEqual(s["logo_state"], "attached")

    def test_writeback_uses_actual_picklist_values(self):
        seen = []
        self.dr._update = lambda recs: seen.extend(recs)
        self.dr.mark_done("1", "run", "held", 3, 1, warnings=["canary"])
        self.assertEqual(seen[0]["Braudit_Trigger_Status"], "partial")
        self.assertEqual(seen[0]["Latest_Braudit_Run_Status"], "Partial")
        self.dr.mark_running("1")
        self.assertEqual(seen[1]["Braudit_Trigger_Status"], "sending")


if __name__ == "__main__":
    unittest.main()


class ZohoExclusionsWriter(unittest.TestCase):
    """audit.exclusions rows → Client_Search_Exclusions payloads (vocabulary of the live module)."""

    def setUp(self):
        from audit_engine import zoho_exclusions
        self.zx = zoho_exclusions

    def test_domain_row(self):
        e = {"id": "e1", "match_kind": "domain", "channel": "domain", "platform": None,
             "value": "zztestwordlogo.example", "reason": "own_asset", "note": "site", "active": True}
        rec = self.zx.record_from_learned(e, "1964745000120522349", run_id="r1", result_id="x1", by="Tester")
        self.assertEqual(rec["Target_Type"], "Domain")
        self.assertEqual(rec["Match_Mode"], "Domain and Subdomains")
        self.assertEqual(rec["Reason"], "Client-Owned")
        self.assertEqual(rec["Platform"], "Domain")
        self.assertEqual(rec["Account"], {"id": "1964745000120522349"})
        self.assertEqual(rec["Braudit_Exclusion_ID"], "e1")
        self.assertEqual(rec["Source_Braudit_Run_ID"], "r1")
        self.assertTrue(rec["Active"])

    def test_trademark_and_social_rows(self):
        tm = self.zx.record_from_learned({"id": "e2", "match_kind": "external_ref", "channel": "trademark",
                                          "value": "UK00003000000", "reason": "unrelated_goods"}, None)
        self.assertEqual(tm["Target_Type"], "Trademark Reference")
        self.assertEqual(tm["Platform"], "Trademark Register")
        # ENGINE_TO_REASON maps unrelated_goods to its own Zoho value rather
        # than collapsing it into "Known False Positive". Both exist on the
        # Client_Search_Exclusions Reason picklist (verified live 17 Sep 2026)
        # and they mean different things: a mark in an unrelated trade is a
        # correct result correctly set aside, not a mistake by the search.
        self.assertEqual(tm["Reason"], "Unrelated Goods")
        self.assertNotIn("Account", tm)
        so = self.zx.record_from_learned({"id": "e3", "match_kind": "handle", "channel": "social",
                                          "platform": "Instagram", "value": "sunrisebakery",
                                          "reason": "staff_judgement"}, "9")
        self.assertEqual(so["Target_Type"], "Handle"); self.assertEqual(so["Sub_Platform"], "Instagram")
        self.assertEqual(so["Reason"], "Previously Reviewed")

    def test_suppressed_writeback_never_calls_zoho(self):
        import os
        os.environ["AUDIT_ZOHO_WRITEBACK"] = "0"
        try:
            called = []
            self.zx.deal_reader.zoho.call = lambda *a, **k: (called.append(a) or {"data": []})
            out = self.zx.upsert([{"id": "e1", "match_kind": "domain", "channel": "domain",
                                   "value": "a.example", "reason": "own_asset"}], "9")
            self.assertEqual(out[0]["action"], "create"); self.assertEqual(out[0]["code"], "SUPPRESSED")
            self.assertTrue(all("search" in str(c[1]) for c in called))   # only the lookups ran
        finally:
            os.environ.pop("AUDIT_ZOHO_WRITEBACK", None)


class SetUpOrderGate(unittest.TestCase):
    def test_send_back_to_setup_payload_and_task(self):
        import os
        from audit_engine import deal_reader
        os.environ["AUDIT_ZOHO_WRITEBACK"] = "0"
        seen = []
        deal_reader._update = lambda recs: seen.extend(recs)
        try:
            deal = dict(HAILAFLO, Owner={"id": "u1"})
            deal_reader.send_back_to_setup(deal, ["no Nice classes (no G S Scope Asset class rows)"])
        finally:
            os.environ.pop("AUDIT_ZOHO_WRITEBACK", None)
        self.assertEqual(seen[0]["Next_Action"], "Set Up Order")
        self.assertEqual(seen[0]["Braudit_Trigger_Status"], "not_ready")
        self.assertTrue(seen[0]["Braudit_Last_Error"].startswith("Missing: no Nice classes"))


def test_triage_assignee_by_deal_type():
    from audit_engine import deal_reader as dr
    deal = {"Owner": {"id": "111"}, "Deal_Name": "ZZ TEST Trademark Audit"}
    assert dr.triage_assignee(deal, "audit") == "111"
    assert dr.triage_assignee(deal, "monitoring") == dr.ADMIN_SUPPORT_USER_ID
    assert dr.triage_assignee({"Deal_Name": "x"}, "audit") == dr.ADMIN_SUPPORT_USER_ID


def test_run_kind_is_pipeline_only():
    from audit_engine import deal_reader as dr
    assert dr.run_kind({"Pipeline": "Monitoring or Representation", "Deal_Name": "Trademark Audit"}) == "monitoring"
    assert dr.run_kind({"Pipeline": {"name": "Monitoring or Representation"}}) == "monitoring"
    assert dr.run_kind({"Pipeline": "Audit and Consultation", "Service": "Monitoring", "Deal_Name": "monitoring"}) == "audit"
    assert dr.run_kind({}) == "audit"


def test_logo_reads_v2_and_v21_file_keys():
    """API v2 returns file_Id/file_Name/original_Size_Byte on a file-upload
    field; v2.1 returns File_Id__s/File_Name__s/Size__s. Both must yield the
    file id the engine fetches the logo bytes with (ZohoCRM.Files.READ)."""
    v2 = {"Image_Mark_Information": [{"id": "r1", "Image_JPEG": [
        {"file_Id": "abc", "file_Name": "l.png", "original_Size_Byte": "12"}]}]}
    v21 = {"Image_Mark_Information": [{"id": "r1", "Image_JPEG": [
        {"File_Id__s": "def", "File_Name__s": "l.jpg", "Size__s": "9"}]}]}
    a, b = deal_fields.logo(v2), deal_fields.logo(v21)
    assert a["state"] == "attached" and a["files"][0]["file_id"] == "abc" and a["files"][0]["name"] == "l.png"
    assert b["files"][0]["file_id"] == "def"
    assert deal_fields.logo({"Logo_Awaited": "true"})["state"] == "awaited"
