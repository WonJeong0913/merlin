import Foundation
import XCTest

@testable import MerlinMac

final class HarnessGovernanceTests: XCTestCase {
    /// The state the harness actually reports today: a frozen 50-task manifest
    /// with no observations yet, no ratio, and promotion blocked on
    /// provider-native invocation evidence.
    private var blockedPayload: [String: BridgeValue] {
        [
            "campaign": .object([
                "artifacts_present": .bool(true),
                "validated": .bool(true),
                "validation_error": .null,
                "campaign_id": .string("merlin-personal-workload-50-longitudinal-v1"),
                "task_count": .number(50),
                "pair_count": .number(100),
                "matched_observation_count": .number(0),
                "lifecycle_change_count": .number(0),
                "g_over_s": .null,
                "g_over_s_status": .string("unavailable-no-verified-direct-savings"),
                "level_7_achieved": .bool(false),
                "level_7_status": .string("not-yet-qualified"),
                "unmet_level_7_checks": .array([.string("promotion_observed")])
            ]),
            "evolution": .object([
                "ledger_present": .bool(false),
                "ledger_path": .string("experiments/mvp/results/x/evolution.jsonl"),
                "reason": .string("no harness-evolution ledger has been generated yet")
            ]),
            "invocation_evidence": .object([
                "harness_signed_events_available": .bool(true),
                "provider_native_evidence_complete": .bool(false),
                "blocking_reason": .string("provider_native_skill_invocation_evidence_incomplete"),
                "consequence": .string("matched observations cannot be promoted")
            ]),
            "lifecycle_operations": .array([
                .object([
                    "kind": .string("promote"),
                    "available": .bool(false),
                    "observed_count": .number(0),
                    "reason": .string("blocked by provider_native_skill_invocation_evidence_incomplete")
                ]),
                .object([
                    "kind": .string("rollback"),
                    "available": .bool(false),
                    "observed_count": .number(0),
                    "reason": .string("no promotion has been recorded")
                ])
            ])
        ]
    }

    func testGovernanceDecodesTheBlockedState() throws {
        let governance = try HarnessGovernance(data: blockedPayload)
        XCTAssertEqual(governance.campaign.taskCount, 50)
        XCTAssertEqual(governance.campaign.pairCount, 100)
        XCTAssertEqual(governance.campaign.matchedObservationCount, 0)
        XCTAssertFalse(governance.campaign.level7Achieved)
        XCTAssertEqual(governance.lifecycleOperations.count, 2)
    }

    /// A null ratio must stay nil rather than collapsing to 0, which the UI
    /// would render as a real measured value.
    func testNullRatioDoesNotBecomeZero() throws {
        let governance = try HarnessGovernance(data: blockedPayload)
        XCTAssertNil(governance.campaign.governanceOverSavings)
        XCTAssertEqual(
            governance.campaign.governanceOverSavingsStatus,
            "unavailable-no-verified-direct-savings"
        )
    }

    /// An absent ledger must not present itself as a ledger with zero
    /// observations.
    func testAbsentLedgerKeepsCountsNil() throws {
        let governance = try HarnessGovernance(data: blockedPayload)
        XCTAssertFalse(governance.evolution.ledgerPresent)
        XCTAssertNil(governance.evolution.observationCount)
        XCTAssertNil(governance.evolution.promotionCount)
    }

    func testBlockingReasonSurvivesDecoding() throws {
        let governance = try HarnessGovernance(data: blockedPayload)
        XCTAssertFalse(governance.invocationEvidence.providerNativeEvidenceComplete)
        XCTAssertEqual(
            governance.invocationEvidence.blockingReason,
            "provider_native_skill_invocation_evidence_incomplete"
        )
        let promote = try XCTUnwrap(
            governance.lifecycleOperations.first { $0.kind == "promote" }
        )
        XCTAssertFalse(promote.available)
    }

    func testMissingSectionsAreRejected() {
        XCTAssertThrowsError(try HarnessGovernance(data: ["campaign": .object([:])]))
    }
}
