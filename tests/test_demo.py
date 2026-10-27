import io
import unittest
from unittest.mock import Mock
import urllib.error

import demo


class StatementTests(unittest.TestCase):
    def test_http_success_with_failed_statement_is_not_query_success(self):
        client = Mock()
        client.request.return_value = {
            "statement_id": "statement-1",
            "status": "failed",
            "error": {"message": "incompatible table"},
        }
        with self.assertRaisesRegex(RuntimeError, "incompatible table"):
            demo.query(client, "CREATE TABLE example (value int)")
        client.request.assert_called_once()

    def test_polling_does_not_resubmit_statement(self):
        client = Mock()
        client.request.side_effect = [
            {"statement_id": "statement-1", "status": "pending"},
            {"statement_id": "statement-1", "status": "finished", "result_set": {"rows": [["1"]]}},
        ]
        self.assertEqual(demo.query(client, "SELECT 1"), {"rows": [["1"]]})
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ["POST", "GET"])


class HTTPTests(unittest.TestCase):
    def test_connection_loss_does_not_replay_mutating_request(self):
        client = demo.Client("http://example.invalid")
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.URLError("connection lost")
        with self.assertRaises(urllib.error.URLError):
            client.request("POST", "/ingest", {"event_id": "18446744073709551615"})
        client.opener.open.assert_called_once()

    def test_rejected_write_keeps_status_and_diagnostic(self):
        client = demo.Client("http://example.invalid")
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.HTTPError(
            "http://example.invalid", 422, "rejected", {},
            io.BytesIO(b'{"append_state":"rejected","message":"integer out of range"}'),
        )
        with self.assertRaises(demo.APIError) as caught:
            client.request("POST", "/rows", {})
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("integer out of range", str(caught.exception))
        client.opener.open.assert_called_once()


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.events = [{"event_id": "18446744073709551615", "service": "checkout",
                        "env": "production", "message": "completed", "duration_ms": 42}]
        self.row = ["2026-10-27T00:00:00Z", "checkout", "production", "run-1",
                    "18446744073709551615", "completed", "42"]

    def test_only_complete_matching_rows_pass(self):
        self.assertFalse(demo.verify_rows([], self.events))
        self.assertTrue(demo.verify_rows([self.row], self.events))

    def test_duplicate_delivery_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate delivery"):
            demo.verify_rows([self.row, self.row], self.events)

    def test_rounded_large_identifier_is_not_success(self):
        rounded = self.row.copy()
        rounded[4] = "18446744073709551616"
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            demo.verify_rows([rounded], self.events)


if __name__ == "__main__":
    unittest.main()
