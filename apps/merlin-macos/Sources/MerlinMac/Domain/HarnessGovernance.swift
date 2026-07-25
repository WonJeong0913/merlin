import Foundation

/// The read-only governance view returned by `harness.governance`.
///
/// Every field mirrors what the bridge read from disk. Absent artifacts stay
/// absent here: nothing in this type substitutes a zero or a default that would
/// read as a positive claim in the UI.
struct HarnessGovernance: Equatable {
    let campaign: CampaignState
    let evolution: EvolutionState
    let invocationEvidence: InvocationEvidence
    let lifecycleOperations: [LifecycleOperation]

    init(data: [String: BridgeValue]) throws {
        guard let campaign = data["campaign"]?.object,
              let evolution = data["evolution"]?.object,
              let invocation = data["invocation_evidence"]?.object else {
            throw BridgeProtocolError.invalidData("harness governance")
        }
        self.campaign = CampaignState(data: campaign)
        self.evolution = EvolutionState(data: evolution)
        invocationEvidence = InvocationEvidence(data: invocation)
        lifecycleOperations = data["lifecycle_operations"]?.array?.compactMap { value in
            value.object.map(LifecycleOperation.init(data:))
        } ?? []
    }

    struct CampaignState: Equatable {
        let artifactsPresent: Bool
        let validated: Bool
        let validationError: String?
        let campaignID: String?
        let manifestSHA256: String?
        let scheduleSHA256: String?
        let taskCount: Int?
        let pairCount: Int?
        let matchedObservationCount: Int?
        let lifecycleChangeCount: Int?
        let governanceOverSavings: Double?
        let governanceOverSavingsStatus: String?
        let level7Achieved: Bool
        let level7Status: String?
        let unmetLevel7Checks: [String]

        init(data: [String: BridgeValue]) {
            artifactsPresent = data["artifacts_present"]?.bool ?? false
            validated = data["validated"]?.bool ?? false
            validationError = data["validation_error"]?.string
            campaignID = data["campaign_id"]?.string
            manifestSHA256 = data["manifest_sha256"]?.string
            scheduleSHA256 = data["schedule_sha256"]?.string
            taskCount = data["task_count"]?.int
            pairCount = data["pair_count"]?.int
            matchedObservationCount = data["matched_observation_count"]?.int
            lifecycleChangeCount = data["lifecycle_change_count"]?.int
            governanceOverSavings = data["g_over_s"]?.double
            governanceOverSavingsStatus = data["g_over_s_status"]?.string
            level7Achieved = data["level_7_achieved"]?.bool ?? false
            level7Status = data["level_7_status"]?.string
            unmetLevel7Checks = data["unmet_level_7_checks"]?.array?.compactMap(\.string) ?? []
        }
    }

    struct EvolutionState: Equatable {
        let ledgerPresent: Bool
        let ledgerPath: String?
        let reason: String?
        let validationError: String?
        let observationCount: Int?
        let promotionCount: Int?
        let rollbackCount: Int?
        let regressionCount: Int?
        let ratioReason: String?

        init(data: [String: BridgeValue]) {
            ledgerPresent = data["ledger_present"]?.bool ?? false
            ledgerPath = data["ledger_path"]?.string
            reason = data["reason"]?.string
            validationError = data["validation_error"]?.string
            observationCount = data["observation_count"]?.int
            promotionCount = data["promotion_count"]?.int
            rollbackCount = data["rollback_count"]?.int
            regressionCount = data["regression_count"]?.int
            ratioReason = data["ratio_reason"]?.string
        }
    }

    struct InvocationEvidence: Equatable {
        let harnessSignedEventsAvailable: Bool
        let providerNativeEvidenceComplete: Bool
        let blockingReason: String?
        let consequence: String?

        init(data: [String: BridgeValue]) {
            harnessSignedEventsAvailable = data["harness_signed_events_available"]?.bool ?? false
            providerNativeEvidenceComplete = data["provider_native_evidence_complete"]?.bool ?? false
            blockingReason = data["blocking_reason"]?.string
            consequence = data["consequence"]?.string
        }
    }

    struct LifecycleOperation: Identifiable, Equatable {
        let kind: String
        let available: Bool
        let observedCount: Int
        let reason: String

        var id: String { kind }

        init(data: [String: BridgeValue]) {
            kind = data["kind"]?.string ?? "unknown"
            available = data["available"]?.bool ?? false
            observedCount = data["observed_count"]?.int ?? 0
            reason = data["reason"]?.string ?? ""
        }
    }
}
