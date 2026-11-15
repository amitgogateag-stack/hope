"""Adversarial tests for denial-only future native-attestation contract."""
import dataclasses
import unittest

from trust_policy import AdmissionExpectation, SupervisorEvidence, evaluate_trust


class TrustPolicyTests(unittest.TestCase):
    def setUp(self):
        self.expected = AdmissionExpectation("generation-A", "release-A", "code-A", "nonce-A")
        self.evidence = SupervisorEvidence("generation-A", "release-A", "code-A", "nonce-A", True)

    def test_even_matching_evidence_does_not_enable_admission(self):
        self.assertEqual(evaluate_trust(self.evidence, self.expected),
                         "DENY_PRODUCTION_GATE_UNIMPLEMENTED")

    def test_no_supervisor(self):
        self.assertEqual(evaluate_trust(None, self.expected),
                         "DENY_UNAUTHENTICATED_SUPERVISOR")

    def test_worker_claim_not_authenticated(self):
        self.assertEqual(evaluate_trust(dataclasses.replace(self.evidence, supervisor_authenticated=False),
                         self.expected), "DENY_UNAUTHENTICATED_SUPERVISOR")

    def test_all_critical_evidence_fields_fail_closed(self):
        for field in ("process_generation", "release_digest", "code_digest", "session_nonce"):
            with self.subTest(field=field):
                missing = dataclasses.replace(self.evidence, **{field: ""})
                self.assertEqual(evaluate_trust(missing, self.expected), "DENY_INCOMPLETE_EVIDENCE")

    def test_same_interpreter_exec_generation_change_denied(self):
        evidence = dataclasses.replace(self.evidence, process_generation="generation-B")
        self.assertEqual(evaluate_trust(evidence, self.expected), "DENY_PROCESS_GENERATION_MISMATCH")

    def test_release_and_loaded_code_mutation_denied(self):
        for field, reason in (("release_digest", "DENY_RELEASE_MISMATCH"),
                              ("code_digest", "DENY_CODE_MISMATCH")):
            with self.subTest(field=field):
                evidence = dataclasses.replace(self.evidence, **{field: "attacker-controlled"})
                self.assertEqual(evaluate_trust(evidence, self.expected), reason)

    def test_replay_into_new_session_denied(self):
        evidence = dataclasses.replace(self.evidence, session_nonce="nonce-other")
        self.assertEqual(evaluate_trust(evidence, self.expected), "DENY_SESSION_MISMATCH")


if __name__ == "__main__":
    unittest.main()
