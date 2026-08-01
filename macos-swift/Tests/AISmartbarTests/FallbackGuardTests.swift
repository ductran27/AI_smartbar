import Foundation
import XCTest
@testable import AISmartbar

final class FallbackGuardTests: XCTestCase {
    func testProtectedRequiresBothFallbacksBlockedAndManualOpusAvailable() throws {
        let report = try decodeReport("""
        {
          "ok": true,
          "state": "protected",
          "protected": true,
          "safetyAutoFallback": "blocked",
          "availabilityAutoFallback": "blocked",
          "manualOpusRestrictedByGuard": false,
          "scope": "local Claude Code sessions on this Mac",
          "claudeVersion": "1.2.3",
          "activeManagedSource": "enterprise",
          "policyPath": "/Library/Managed Preferences/com.anthropic.claude.plist",
          "details": [],
          "lastLiveCheck": {
            "status": "passed",
            "checkedAt": "2026-07-31T12:00:00Z",
            "claudeVersion": "1.2.3",
            "totalCostUsd": 0.04,
            "budgetLimitUsd": 0.25,
            "probes": []
          }
        }
        """)

        XCTAssertTrue(report.hasCompletePolicy)
        XCTAssertTrue(report.isFullyProtected)
        XCTAssertEqual(report.presentationState, .protected)
        XCTAssertEqual(report.safetyAutoFallback, .blocked)
        XCTAssertEqual(report.availabilityAutoFallback, .blocked)
    }

    func testProtectedInconclusiveStaysDistinctFromVerifiedProtection() throws {
        let report = try decodeReport("""
        {
          "ok": true,
          "state": "protected_inconclusive",
          "protected": true,
          "safetyAutoFallback": "blocked",
          "availabilityAutoFallback": "blocked",
          "manualOpusRestrictedByGuard": false,
          "scope": "local",
          "claudeVersion": "1.2.3",
          "activeManagedSource": "enterprise",
          "policyPath": "/tmp/policy",
          "details": "Live check could not identify the serving model",
          "lastLiveCheck": {
            "status": "inconclusive",
            "checkedAt": "2026-07-31T12:00:00Z",
            "claudeVersion": "1.2.3",
            "totalCostUsd": 0.03,
            "budgetLimitUsd": 0.25,
            "probes": []
          }
        }
        """)

        XCTAssertTrue(report.hasCompletePolicy)
        XCTAssertFalse(report.isFullyProtected)
        XCTAssertEqual(report.presentationState, .protectedInconclusive)
        XCTAssertEqual(report.details, ["Live check could not identify the serving model"])
    }

    func testPassingCheckFromOlderClaudeVersionIsNotGreen() throws {
        let report = try decodeReport("""
        {
          "ok": true,
          "state": "protected",
          "protected": true,
          "safetyAutoFallback": "blocked",
          "availabilityAutoFallback": "blocked",
          "manualOpusRestrictedByGuard": false,
          "scope": "local",
          "claudeVersion": "2.0.0",
          "activeManagedSource": "enterprise",
          "policyPath": "/tmp/policy",
          "details": [],
          "lastLiveCheck": {
            "status": "passed",
            "checkedAt": "2026-07-31T12:00:00Z",
            "claudeVersion": "1.9.0",
            "totalCostUsd": 0.03,
            "budgetLimitUsd": 0.25,
            "probes": []
          }
        }
        """)

        XCTAssertTrue(report.hasCompletePolicy)
        XCTAssertFalse(report.isFullyProtected)
        XCTAssertEqual(report.presentationState, .protectedInconclusive)
    }

    func testConflictCannotRenderGreenEvenWhenStateSaysProtected() throws {
        let report = try decodeReport("""
        {
          "ok": true,
          "state": "protected",
          "protected": true,
          "safetyAutoFallback": "blocked",
          "availabilityAutoFallback": "enabled",
          "manualOpusRestrictedByGuard": true,
          "scope": "local",
          "claudeVersion": "1.2.3",
          "activeManagedSource": "enterprise",
          "policyPath": "/tmp/policy",
          "details": ["Availability fallback remains enabled"],
          "lastLiveCheck": null
        }
        """)

        XCTAssertFalse(report.hasCompletePolicy)
        XCTAssertFalse(report.isFullyProtected)
        XCTAssertEqual(report.presentationState, .actionNeeded)
    }

    func testBooleanFallbackFieldsDecodeDefensively() throws {
        let report = try decodeReport("""
        {
          "ok": 1,
          "state": "protected",
          "protected": "true",
          "safetyAutoFallback": false,
          "availabilityAutoFallback": false,
          "manualOpusRestrictedByGuard": "false",
          "scope": "local",
          "claudeVersion": "1.2.3",
          "activeManagedSource": "enterprise",
          "policyPath": "/tmp/policy",
          "details": {},
          "lastLiveCheck": null
        }
        """)

        XCTAssertTrue(report.ok)
        XCTAssertTrue(report.protected)
        XCTAssertEqual(report.safetyAutoFallback, .blocked)
        XCTAssertEqual(report.availabilityAutoFallback, .blocked)
        XCTAssertEqual(report.manualOpusRestrictedByGuard, false)
        XCTAssertEqual(report.presentationState, .protectedInconclusive)
    }

    func testNotProtectedAndUnknownValuesFailClosed() throws {
        let unprotected = try decodeReport("""
        {
          "ok": true,
          "state": "not_protected",
          "protected": false,
          "safetyAutoFallback": "enabled",
          "availabilityAutoFallback": "enabled",
          "manualOpusRestrictedByGuard": false,
          "scope": "local",
          "claudeVersion": "1.2.3",
          "activeManagedSource": "none",
          "policyPath": "",
          "details": [],
          "lastLiveCheck": null
        }
        """)
        XCTAssertEqual(unprotected.presentationState, .notProtected)

        let unknown = try decodeReport("""
        {
          "ok": true,
          "state": "protected",
          "protected": true,
          "safetyAutoFallback": "unknown",
          "availabilityAutoFallback": "blocked",
          "manualOpusRestrictedByGuard": false,
          "scope": "local",
          "claudeVersion": "1.2.3",
          "activeManagedSource": "enterprise",
          "policyPath": "/tmp/policy",
          "details": [],
          "lastLiveCheck": null
        }
        """)
        XCTAssertEqual(unknown.presentationState, .actionNeeded)
    }

    func testLiveCheckDecodesOnlyOutcomeMetadata() throws {
        let report = try decodeReport("""
        {
          "ok": true,
          "state": "protected",
          "protected": true,
          "safetyAutoFallback": "blocked",
          "availabilityAutoFallback": "blocked",
          "manualOpusRestrictedByGuard": false,
          "scope": "local",
          "claudeVersion": "1.2.3",
          "activeManagedSource": "enterprise",
          "policyPath": "/tmp/policy",
          "details": [],
          "lastLiveCheck": {
            "status": "passed",
            "checkedAt": "2026-07-31T12:00:00Z",
            "claudeVersion": "1.2.3",
            "totalCostUsd": 0.07,
            "budgetLimitUsd": 0.25,
            "probes": [{
              "name": "manual_opus",
              "outcome": "OPUS_OK",
              "requestedModel": "opus",
              "observedModels": ["opus"],
              "costUsd": 0.02,
              "requestId": "req_123"
            }]
          }
        }
        """)

        let check = try XCTUnwrap(report.lastLiveCheck)
        XCTAssertEqual(check.probes.count, 1)
        XCTAssertEqual(check.probes[0].observedModels, ["opus"])
        XCTAssertNotNil(check.checkedDate)
        XCTAssertEqual(check.totalCostUsd, 0.07)
        XCTAssertTrue(report.manualOpusVerifiedAvailable)
    }

    func testCLIArgumentsUseSharedFallbackGuardCommands() {
        XCTAssertEqual(FallbackGuardCLIClient.arguments(for: .status),
                       ["--fallback-guard", "status"])
        XCTAssertEqual(FallbackGuardCLIClient.arguments(for: .enable),
                       ["--fallback-guard", "enable"])
        XCTAssertEqual(FallbackGuardCLIClient.arguments(for: .verify),
                       ["--fallback-guard", "verify"])
        XCTAssertEqual(FallbackGuardCLIClient.arguments(for: .remove),
                       ["--fallback-guard", "remove"])
    }

    private func decodeReport(_ json: String) throws -> FallbackGuardReport {
        try JSONDecoder().decode(FallbackGuardReport.self,
                                 from: Data(json.utf8))
    }
}
