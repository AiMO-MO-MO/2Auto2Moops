import unittest

from core.dedup import match_customer, normalize_email, normalize_phone
from core.efs import expand_kit_for_efs, map_sor_to_efs_shipping, route_products_for_efs
from core.moops import build_tag
from core.order_plan import audit_hardware_requirements, build_system_rerun_plan
from playbooks.cards_order import build_cards_tag
from playbooks.parts_order import build_parts_tag
from playbooks.salesforce import build_plan, opportunity_name, to_e164
from run import (
    _apply_config_attachment_signal,
    _can_run_chain_from_snapshot,
    _card_type,
    _existing_card_blocks_card_clone,
    _existing_chain_done_statuses,
    _expand_verb,
    _location_match_score,
)


class CommandRoutingTests(unittest.TestCase):
    def test_system_shorthand_routes_to_idempotent_no_itf_flow(self):
        self.assertEqual(
            _expand_verb(["run.py", "s", "19697"]),
            ["run.py", "--so-id", "19697", "--first-touch", "--no-itf", "--dedup-test"],
        )

    def test_parts_and_cards_shorthand(self):
        self.assertEqual(
            _expand_verb(["run.py", "p", "19697"]),
            ["run.py", "--so-id", "19697", "--parts-order"],
        )
        self.assertEqual(
            _expand_verb(["run.py", "c", "19697", "WASHCO"]),
            ["run.py", "--so-id", "19697", "--cards-order", "WASHCO"],
        )

    def test_snapshot_shorthand_is_read_only_plan(self):
        self.assertEqual(
            _expand_verb(["run.py", "snapshot", "19697"]),
            ["run.py", "--so-id", "19697", "--snapshot"],
        )

    def test_chain_only_snapshot_skips_first_touch_when_customer_is_known(self):
        self.assertTrue(_can_run_chain_from_snapshot({
            "effective_customer_id": "00750",
            "actionable": ["Task 9 To Do -> link SO End Customer/location if needed, then upload VAC config files"],
        }))
        self.assertFalse(_can_run_chain_from_snapshot({
            "effective_customer_id": "00750",
            "actionable": ["Task 1 not Completed -> add missing hardware companion parts"],
        }))
        self.assertFalse(_can_run_chain_from_snapshot({
            "effective_customer_id": "",
            "actionable": ["Task 8 To Do -> add/verify Portal location"],
        }))

    def test_existing_chain_task9_only_updates_only_task9(self):
        self.assertEqual(
            _existing_chain_done_statuses(did_config=True),
            {9: "Completed"},
        )

    def test_missing_config_files_force_config_even_if_task_completed(self):
        plan = {
            "skip": ["Task 9 Completed -> skip SO End Customer/config workflow"],
            "actionable": [],
            "inputs": [],
            "effective_customer_id": "00750",
        }
        _apply_config_attachment_signal(
            plan,
            {"products": [{"part_number": "VAC07-42-20", "qty": 3}]},
            {9: {"status": "Completed"}},
            {"id": "00750"},
            [],
        )
        self.assertTrue(plan["force_config"])
        self.assertNotIn("Task 9 Completed -> skip SO End Customer/config workflow", plan["skip"])
        self.assertTrue(any("Task 9 config workflow needed" in item for item in plan["actionable"]))

    def test_missing_config_files_force_link_then_config_when_customer_only_from_sor(self):
        plan = {
            "skip": [],
            "actionable": [],
            "inputs": [],
            "effective_customer_id": "00750",
        }
        _apply_config_attachment_signal(
            plan,
            {"products": [{"part_number": "VAC07-42-20", "qty": 3}]},
            {9: {"status": "To Do"}},
            {},
            ["one.cfg"],
        )
        self.assertTrue(plan["force_config"])
        self.assertEqual(plan["config_files"]["attached"], 1)
        self.assertEqual(plan["config_files"]["expected"], 3)


class CardHelpersTests(unittest.TestCase):
    def test_card_type_uses_dropdown_prefixes(self):
        self.assertEqual(_card_type("New design"), "new")
        self.assertEqual(_card_type("Modify existing card"), "modify")
        self.assertEqual(_card_type("Reprint"), "reprint")
        self.assertEqual(_card_type("Existing"), "reprint")
        self.assertEqual(_card_type(""), "none")

    def test_existing_card_only_blocks_new_design_clone_not_modify(self):
        self.assertTrue(_existing_card_blocks_card_clone("new"))
        self.assertFalse(_existing_card_blocks_card_clone("modify"))

    def test_cards_tag_falls_back_to_card_description_name(self):
        tag = build_cards_tag(
            [{"part_number": "CARD-MD-ABC", "qty": "1000", "description": "Laundry Depot II card\nBlue"}],
            "",
        )
        self.assertEqual(tag, "1000 Cards (Laundry Depot II)")


class OrderTagTests(unittest.TestCase):
    def test_system_tag_preserves_product_order(self):
        tag = build_tag(
            [
                {"part_number": "VAC07-10-00", "qty": "2"},
                {"part_number": "VAC02-00-00", "qty": "1"},
                {"part_number": "CR-11-100", "qty": "4"},
            ],
            "MAIN STREET LAUNDRY",
        )
        self.assertEqual(tag, "2 VAC07, 1 VAC02, 4 Readers (Main Street Laundry)")

    def test_system_tag_counts_reader_equiv_kits(self):
        # CR-* readers PLUS reader-equivalent kits (POS / MDB vending / door access / vending) count.
        tag = build_tag(
            [
                {"part_number": "VAC07-42-20", "qty": "2"},
                {"part_number": "CR-10-150-00", "qty": "35"},
                {"part_number": "KIT-VENDRITE-01", "qty": "1"},
            ],
            "WASH ZONE",
        )
        self.assertEqual(tag, "2 VAC07, 36 Readers (Wash Zone)")

    def test_parts_tag_itemizes_kits_not_folded(self):
        # Parts orders itemize kits by TYPE -- they are NOT folded into the Reader Kit count
        # (that fold is system-tag only). 33 CR readers stay 33; door access is listed on its own.
        tag = build_parts_tag(
            [
                {"part_number": "CR-10-150-00", "qty": "33", "description": ""},
                {"part_number": "KIT-DOORACCESS-02", "qty": "2", "description": "Door Access Kit"},
            ],
            "TEST CO",
        )
        self.assertIn("33 Reader Kits", tag)   # NOT 35 -- door access is not folded into the count
        self.assertIn("Door Access", tag)      # listed as its own kit type


class LocationMatchTests(unittest.TestCase):
    def test_location_match_scores_address_city_state_zip(self):
        score = _location_match_score(
            "Clean Rite Center\n586 Coney Island Avenue\nBrooklyn, NY, 11218, United States",
            {"address": "586 Coney Island Avenue", "city": "Brooklyn", "state": "NY", "zip": "11218"},
        )
        self.assertGreaterEqual(score, 6)

    def test_location_match_scores_portal_index_address(self):
        score = _location_match_score(
            "442 39th Street, Brooklyn, NY, 11232",
            {"address": "442 39th Street, Brooklyn, NY"},
        )
        self.assertGreaterEqual(score, 6)

    def test_location_match_rejects_different_street(self):
        score = _location_match_score(
            "586 Coney Island Avenue\nBrooklyn, NY, 11218",
            {"address": "100 Main Street", "city": "Brooklyn", "state": "NY", "zip": "11218"},
        )
        self.assertLess(score, 6)


class EfsHelpersTests(unittest.TestCase):
    def test_kit_expansion_and_shipping_mapping(self):
        self.assertEqual(
            expand_kit_for_efs("KIT-A35", 2),
            [{"part": "03-01-95", "qty": 2}, {"part": "01-02-23", "qty": 2}],
        )
        self.assertEqual(map_sor_to_efs_shipping("Ground"), "FedEx Ground")
        self.assertEqual(map_sor_to_efs_shipping("Ground", "please overnight"), "FedEx Standard Overnight")

    def test_route_products_for_efs_splits_expandable_and_other(self):
        efs, other = route_products_for_efs(
            [{"part": "KIT-A35", "qty": 1}, {"part": "KIT-P630", "qty": 1}]
        )
        self.assertEqual(efs, [{"part": "03-01-95", "qty": 1}, {"part": "01-02-23", "qty": 1}])
        self.assertEqual(other, [{"part": "KIT-P630", "qty": 1}])


class DedupTests(unittest.TestCase):
    def test_strong_email_match_wins(self):
        self.assertEqual(normalize_email("Contact <A@Example.com>"), "a@example.com")
        self.assertEqual(normalize_phone("+1 (508) 555-1212"), "5085551212")
        result = match_customer(
            {"customer_name": "Fresh Wash", "contact_name": "Ana Smith",
             "contact_email": "ana@example.com", "contact_phone": ""},
            [{"cust_id": "01234", "name": "Fresh Wash",
              "contact_name": "Ana Smith", "contact_email": "ana@example.com",
              "contact_phone": "5085551212"}],
        )
        self.assertEqual(result["verdict"], "existing")
        self.assertEqual(result["matches"][0]["cust_id"], "01234")


class SalesforcePlanTests(unittest.TestCase):
    def test_salesforce_plan_is_pure_data(self):
        self.assertEqual(to_e164("(508) 555-1212"), "+15085551212")
        self.assertEqual(opportunity_name("3400 West Vine Street", 19871), "3400 Vine-Moops-SO-19871")
        plan = build_plan(
            19871,
            {
                "location_name": "Main Street Laundry",
                "location_address": "Main Street Laundry\n3400 West Vine Street\nSpringfield, MA 01103",
                "contact_name": "Ana Smith",
                "contact_email": "ana@example.com",
                "contact_phone": "(508) 555-1212",
            },
            "01234",
            [{"part_number": "KIT-POS-01"}],
        )
        self.assertEqual(plan["account"]["LW_account_ID__c"], "01234")
        self.assertIsNotNone(plan["cents_pos_opp"])


class OrderPlanTests(unittest.TestCase):
    def test_hardware_audit_checks_companion_parts_independently_of_task_state(self):
        audit = audit_hardware_requirements(
            {
                "is_route": False,
                "products": [
                    {"part_number": "VAC07-42-20", "qty": 2},
                    {"part_number": "KIT-P630", "qty": 2},
                    {"part_number": "03-01-34", "qty": 2},
                    {"part_number": "CARD-03-01", "qty": 1},
                    {"part_number": "SVC-LAUNDROMAT", "qty": 1},
                ],
            },
            {"processor_type": "No - Stripe (Standard)"},
        )
        self.assertTrue(audit["ready"])
        self.assertEqual(audit["missing"], [])

    def test_hardware_audit_filters_review_only_missing_associations(self):
        audit = audit_hardware_requirements(
            {
                "is_route": False,
                "products": [
                    {"part_number": "VAC07-42-20", "qty": 2},
                    {"part_number": "KIT-P630", "qty": 2},
                    {"part_number": "03-01-34", "qty": 2},
                    {"part_number": "CARD-03-01", "qty": 1},
                    {"part_number": "SVC-LAUNDROMAT", "qty": 1},
                ],
                "missing": [
                    {"part_number": "CR-10-126-28", "associated_part": "02-06-78DL",
                     "qty": "31", "description": "LONG version cable"},
                    {"part_number": "VAC07-42-20", "associated_part": "CARD-03-01",
                     "qty": "2", "description": "System cards"},
                ],
            },
            {"processor_type": "No - Stripe (Standard)"},
        )
        self.assertTrue(audit["ready"])
        self.assertEqual(audit["missing_associations"], [])
        self.assertTrue(any("long cable review-only" in item for item in audit["skipped_associations"]))

    def test_task6_saas_handoff_is_actionable_when_only_task6_remains(self):
        # Task 6 (SaaS) is now automated in the system run: the chain posts the order to the
        # #moops-matt-mark Slack channel. A touched order with only task 6 left must be ACTIONABLE
        # so the run proceeds to the chain and posts, instead of short-circuiting to "nothing to do".
        tasks = {
            1: {"status": "Completed"}, 2: {"status": "Completed"},
            3: {"status": "N/A"}, 4: {"status": "N/A"}, 5: {"status": "N/A"},
            6: {"status": "To Do"}, 7: {"status": "Completed"},
            8: {"status": "Completed"}, 9: {"status": "Completed"},
            10: {"status": "Completed"},
        }
        plan = build_system_rerun_plan(
            {"tag": "2 VAC07 (SpinXpress)", "assembly_week": "2026-06-15", "is_route": False},
            {"card_design_type": ""},
            tasks,
            {"id": "00121"},
        )
        self.assertIn("Task 6 To Do -> post SaaS handoff to #moops-matt-mark (Slack)",
                      plan["actionable"])
        self.assertFalse(any("Salesforce" in b for b in plan["blocked"]))

    def test_route_plan_skips_system_provisioning(self):
        tasks = {n: {"status": "To Do"} for n in range(1, 11)}
        plan = build_system_rerun_plan(
            {"tag": "15 VAC02 (Sparkle)", "assembly_week": "2026-06-29", "is_route": True},
            {"card_design_type": ""},
            tasks,
            {},
        )
        self.assertIn("Route order -> skip SaaS/payment/location/user provisioning", plan["skip"])
        self.assertIn("Route task checklist needs 1-2 Completed and 3-10 N/A", plan["actionable"])
        self.assertFalse(any("Portal location" in item for item in plan["actionable"]))

    def test_reprint_card_is_skipped_when_card_tasks_are_already_resolved(self):
        tasks = {
            1: {"status": "To Do"}, 2: {"status": "To Do"},
            3: {"status": "N/A"}, 4: {"status": "N/A"}, 5: {"status": "Completed"},
            6: {"status": "To Do"}, 7: {"status": "To Do"},
            8: {"status": "To Do"}, 9: {"status": "To Do"},
            10: {"status": "To Do"},
        }
        plan = build_system_rerun_plan(
            {"tag": "2 VAC07 (Supermatt)", "assembly_week": "2026-06-15", "is_route": False},
            {"card_design_type": "Re-print of existing design"},
            tasks,
            {},
        )
        self.assertIn("Card tasks 3-5 already resolved -> skip card workflow", plan["skip"])
        self.assertFalse(any("Card workflow" in item for item in plan["actionable"]))

    def test_card_po_on_card_row_skips_card_workflow(self):
        tasks = {
            1: {"status": "To Do"}, 2: {"status": "To Do"},
            3: {"status": "To Do"}, 4: {"status": "To Do"}, 5: {"status": "To Do"},
            6: {"status": "To Do"}, 7: {"status": "To Do"},
            8: {"status": "To Do"}, 9: {"status": "To Do"},
            10: {"status": "To Do"},
        }
        plan = build_system_rerun_plan(
            {
                "tag": "2 VAC07 (Supermatt)",
                "assembly_week": "2026-06-15",
                "is_route": False,
                "products": [{"part_number": "CARD-MD-SPRMATT", "has_po": True}],
            },
            {"card_design_type": "Re-print of existing design"},
            tasks,
            {},
        )
        self.assertIn("Card PO exists on CARD-MD row -> skip card workflow", plan["skip"])
        self.assertFalse(any("Card workflow" in item for item in plan["actionable"]))

    def test_required_inputs_show_blocked_location_and_user_data(self):
        tasks = {
            1: {"status": "To Do"}, 2: {"status": "To Do"},
            3: {"status": "N/A"}, 4: {"status": "N/A"}, 5: {"status": "Completed"},
            6: {"status": "To Do"}, 7: {"status": "To Do"},
            8: {"status": "To Do"}, 9: {"status": "To Do"},
            10: {"status": "To Do"},
        }
        plan = build_system_rerun_plan(
            {
                "tag": "2 VAC07 (Supermatt)",
                "assembly_week": "2026-06-15",
                "customer_name": "Colleton Drive Sarasota",
                "is_route": False,
                "products": [{"part_number": "VAC07-42-20", "qty": 2}],
                "missing": [{"part_number": "VAC07-42-20"}],
            },
            {"card_design_type": "Re-print of existing design", "processor_type": "No - Stripe (Standard)"},
            tasks,
            {},
        )
        inputs = {item["step"]: item for item in plan["inputs"]}
        self.assertTrue(inputs["Task 1 hardware"]["ready"])
        self.assertFalse(inputs["Task 8 location"]["ready"])
        self.assertFalse(inputs["Task 10 user/intro"]["ready"])
        self.assertFalse(inputs["Task 9 config"]["ready"])
        self.assertIn("Task 10 To Do but contact name/email/phone are incomplete",
                      plan["hard_blocked"])
        self.assertFalse(any("Salesforce" in item for item in plan["hard_blocked"]))

    def test_sor_existing_end_customer_unblocks_customer_dependent_steps(self):
        tasks = {
            1: {"status": "Completed"}, 2: {"status": "To Do"},
            3: {"status": "N/A"}, 4: {"status": "N/A"}, 5: {"status": "Completed"},
            6: {"status": "To Do"}, 7: {"status": "To Do"},
            8: {"status": "To Do"}, 9: {"status": "To Do"},
            10: {"status": "To Do"},
        }
        plan = build_system_rerun_plan(
            {
                "tag": "2 VAC07 (Supermatt)",
                "assembly_week": "2026-06-15",
                "customer_name": "Colleton Drive Sarasota",
                "is_route": False,
                "products": [{"part_number": "VAC07-42-20", "qty": 2}],
                "missing": [],
            },
            {
                "processor_type": "No - Stripe (Standard)",
                "location_address": "1046 Colleton Drive",
                "existing_end_customer": "Supermatt (00795)",
                "existing_end_customer_id": "00795",
            },
            tasks,
            {},
        )
        inputs = {item["step"]: item for item in plan["inputs"]}
        self.assertEqual(plan["effective_customer_id"], "00795")
        self.assertFalse(inputs["Task 7 payment"]["ready"])
        self.assertFalse(inputs["Task 9 config"]["ready"])
        self.assertIn("Task 8 To Do -> add/verify Portal location", plan["actionable"])
        self.assertIn("Task 7 To Do but Portal location is not completed yet", plan["blocked"])
        self.assertIn("Task 9 To Do but Portal location is not completed yet", plan["blocked"])
        self.assertNotIn("Task 7 To Do but Portal location is not completed yet", plan["hard_blocked"])
        self.assertNotIn("Task 9 To Do but Portal location is not completed yet", plan["hard_blocked"])
        self.assertIn("Existing customer identified -> skip final Portal user/intro email", plan["skip"])
        self.assertFalse(any("Task 10" in item for item in plan["hard_blocked"]))

    def test_so_end_customer_skips_user_intro_when_location_still_needed(self):
        tasks = {n: {"status": "To Do"} for n in range(1, 11)}
        plan = build_system_rerun_plan(
            {
                "tag": "",
                "assembly_week": "",
                "customer_name": "LUTFI LAUNDROMATS BESSEMER",
                "is_route": False,
                "products": [{"part_number": "VAC08-42-20", "qty": 1}],
                "missing": [],
            },
            {
                "processor_type": "No - Stripe (Standard)",
                "location_address": "100 Main Street",
            },
            tasks,
            {"id": "02086", "name": "Midfield Laundromat"},
        )
        self.assertIn("Existing customer identified -> skip final Portal user/intro email", plan["skip"])
        self.assertIn("Task 8 To Do -> add/verify Portal location", plan["actionable"])
        self.assertFalse(any("Task 10" in item for item in plan["hard_blocked"]))

    def test_all_todo_new_customer_system_can_run_same_pass_dependencies(self):
        tasks = {n: {"status": "To Do"} for n in range(1, 11)}
        plan = build_system_rerun_plan(
            {
                "tag": "",
                "assembly_week": "",
                "customer_name": "New Laundry",
                "is_route": False,
                "products": [{"part_number": "VAC07-42-20", "qty": 2}],
                "missing": [],
            },
            {
                "processor_type": "No - Stripe (Standard)",
                "card_design_type": "New design",
                "location_name": "New Laundry",
                "location_address": "100 Main Street",
                "contact_name": "Ana Smith",
                "contact_email": "ana@example.com",
            },
            tasks,
            {},
        )
        inputs = {item["step"]: item for item in plan["inputs"]}
        self.assertIn("Tag missing -> set tag", plan["actionable"])
        self.assertIn("Assembly week missing -> pick/set week", plan["actionable"])
        self.assertIn("Task 8 To Do -> add/verify Portal location", plan["actionable"])
        self.assertIn("Task 7 To Do but Portal location is not completed yet", plan["blocked"])
        self.assertIn("Task 9 To Do but customer/location will be created in this run", plan["blocked"])
        self.assertIn("Task 10 To Do -> final Portal user/intro email", plan["actionable"])
        self.assertIn("Card workflow may be needed from SOR card type: new", plan["actionable"])
        self.assertFalse(inputs["Task 7 payment"]["ready"])
        self.assertFalse(inputs["Task 9 config"]["ready"])
        self.assertEqual(plan["hard_blocked"], [])


if __name__ == "__main__":
    unittest.main()
