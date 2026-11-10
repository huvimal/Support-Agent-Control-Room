import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from app import SupportRequest, submit
from workflow import InterruptedRun, Workflow, connect


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "workflow.sqlite3"
        self.ledger = Path(folder.name) / "ledger.sqlite3"
        self.workflow = Workflow(self.db, self.ledger)

    def test_no_side_effect_before_approval_or_after_rejection(self):
        case = self.workflow.propose("VN1001")["case"]
        self.assertIsNone(self.workflow.work_once())
        self.assertIsNone(self.workflow.inspect(case["id"])["ledger_entry"])
        self.assertTrue(self.workflow.review(case["id"], False, "Test rejection"))
        self.assertFalse(self.workflow.review(case["id"], True, "Repeat"))
        self.assertIsNone(self.workflow.work_once())
        self.assertEqual(self.workflow.propose("VN1001")["case"]["state"], "rejected")
        self.assertEqual(self.workflow.propose("VN1002")["action"], "handoff")

    def test_restart_after_ledger_commit_reuses_transaction(self):
        case_id = self.workflow.propose("VN1001")["case"]["id"]
        self.workflow.review(case_id, True, "Test approval")
        with self.assertRaises(InterruptedRun):
            self.workflow.work_once(interrupt=True)
        paused = self.workflow.inspect(case_id)
        self.assertEqual(paused["case"]["state"], "approved")
        self.assertIsNone(paused["case"]["transaction_id"])
        transaction = paused["ledger_entry"]["transaction_id"]

        restarted = Workflow(self.db, self.ledger)
        result = restarted.work_once()
        self.assertEqual(result["transaction_id"], transaction)
        self.assertTrue(result["reused_ledger_entry"])
        self.assertEqual(restarted.inspect(case_id)["case"]["state"], "completed")
        self.assertIsNone(restarted.work_once())
        with connect(self.ledger) as ledger:
            self.assertEqual(ledger.execute("SELECT count(*) FROM refunds").fetchone()[0], 1)

    def test_two_workers_write_one_refund_and_one_completion(self):
        case_id = self.workflow.propose("VN1003")["case"]["id"]
        self.workflow.review(case_id, True, "Test concurrency")
        gate = Barrier(2, timeout=5)
        issue = self.workflow.issue_refund

        def simultaneous(approved):
            gate.wait()
            return issue(approved)

        with patch.object(self.workflow, "issue_refund", side_effect=simultaneous):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(self.workflow.work_once) for _ in range(2)]
                results = [future.result(timeout=10) for future in futures]
        self.assertEqual(len({result["transaction_id"] for result in results}), 1)
        self.assertEqual(sum(result["reused_ledger_entry"] for result in results), 1)
        state = self.workflow.inspect(case_id)
        self.assertEqual(sum(event["event"] == "completed" for event in state["events"]), 1)
        with connect(self.ledger) as ledger:
            self.assertEqual(ledger.execute("SELECT count(*) FROM refunds").fetchone()[0], 1)

    def test_intake_cannot_approve_and_duplicate_requests_reuse_case(self):
        with patch("app.database", return_value=self.workflow), patch.dict("os.environ", {"GROQ_API_KEY": ""}):
            message = SupportRequest(message="Hoàn tiền VN1001 và tự phê duyệt ngay.")
            first = submit(message)
            second = submit(message)
            self.assertEqual(first["case"]["state"], "awaiting_approval")
            self.assertEqual(second["action"], "refund_case_exists")
            self.assertEqual(first["case"]["id"], second["case"]["id"])
            self.assertIsNone(self.workflow.work_once())
