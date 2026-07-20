// Live countdown from an absolute ISO-8601 reset time — mirror of
// smartbar/core/reset_countdown_format.py; format matches cswap's
// oauth.format_reset ("44m", "1h 44m", "6d 13h", clamped at "0m").
import Foundation

enum TimeRemaining {
    /// Tolerant ISO-8601 parse: cswap emits fractional seconds
    /// ("…T05:40:00.162682+00:00") and a "Z" suffix, both of which trip
    /// ISO8601DateFormatter's fixed format — strip the fraction first.
    static func parseISO(_ text: String) -> Date? {
        guard !text.isEmpty else { return nil }
        var cleaned = text
        if let fraction = cleaned.range(of: #"\.\d+"#, options: .regularExpression) {
            cleaned.removeSubrange(fraction)
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: cleaned)
    }

    /// Countdown text to `resetsAt` at `now`, or nil when unparseable
    /// (callers fall back to cswap's fetch-time string — stale beats blank).
    static func countdown(to resetsAt: String, now: Date = Date()) -> String? {
        guard let resets = parseISO(resetsAt) else { return nil }
        let total = max(0, Int(resets.timeIntervalSince(now)))
        let days = total / 86400
        let hours = (total % 86400) / 3600
        let minutes = (total % 3600) / 60
        if days > 0 { return "\(days)d \(hours)h" }
        if hours > 0 { return "\(hours)h \(minutes)m" }
        return "\(minutes)m"
    }
}
