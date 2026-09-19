"""Acceptance test shipped with the portable handoff; no project import needed."""

import json
import unittest
from pathlib import Path

from reference_engine import Engine, format_history_complement


HERE = Path(__file__).resolve().parent


class ReferenceEngineTest(unittest.TestCase):
    def test_example_is_reproducible(self):
        payload = json.loads((HERE / "example-input.json").read_text(encoding="utf-8"))
        expected = json.loads((HERE / "example-output.json").read_text(encoding="utf-8"))
        self.assertEqual(Engine(payload).generate(), expected)

    def test_complement_does_not_invent_evidence(self):
        row = {
            "amount": "-11078.78",
            "counterparty": "00015-AIRLIQUIDEBRASILLTDA-PAULINIA",
            "notes": "PIX ENVIADO",
            "document": "0000355193",
        }
        self.assertEqual(
            format_history_complement(row),
            "PAGAMENTO PIX - AIRLIQUIDEBRASILLTDA-PAULINIA - DOC 0000355193",
        )

    def test_local_account_and_hist_heads_keep_their_probabilities_separate(self):
        payload = json.loads((HERE / "example-input.json").read_text(encoding="utf-8"))
        payload["history"] = []
        payload["movements"][0].update({"amount": "50", "notes": "recebimento", "document": None})
        entry = Engine(payload).generate()["entries"][0]
        self.assertEqual(entry["debit_account"], "1000012")
        self.assertEqual(entry["credit_account"], "2000001")
        self.assertEqual(entry["standard_history"], "551")
        self.assertEqual(entry["account_probability"], 1.0)
        self.assertEqual(entry["history_probability"], 1.0)
        self.assertEqual(entry["account_probability_source"], "exact_memory")
        self.assertEqual(entry["history_probability_source"], "exact_memory")


if __name__ == "__main__":
    unittest.main()
