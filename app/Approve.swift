// ntfsmnt-approve — optional Touch ID approval for an ntfsmnt grant.
//
// The root controller cannot raise a biometric prompt; this helper runs in the
// user's GUI session. It reads one JSON object on stdin:
//
//   {"url": "...", "request_id": "r_...", "nonce": "...",
//    "volume": "HD1", "actions": ["mount"]}
//
// Exit codes: 0 approved, 3 denied, 2 unavailable (the adapter falls back to
// the browser). The nonce is the authority; Touch ID is only the gesture.
//
// Build: swiftc -O -framework LocalAuthentication app/Approve.swift \
//          -o dist/ntfsmnt.app/Contents/MacOS/ntfsmnt-approve

import Foundation
import LocalAuthentication

func die(_ message: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

func post(_ url: URL, nonce: String, origin: String) -> Int? {
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue(nonce, forHTTPHeaderField: "X-NTFSMNT-APPROVAL")
    request.setValue("1", forHTTPHeaderField: "X-NTFSMNT")
    request.setValue(origin, forHTTPHeaderField: "Origin")
    request.httpBody = try? JSONSerialization.data(withJSONObject: ["nonce": nonce])

    let semaphore = DispatchSemaphore(value: 0)
    var status: Int?
    URLSession.shared.dataTask(with: request) { _, response, _ in
        status = (response as? HTTPURLResponse)?.statusCode
        semaphore.signal()
    }.resume()
    semaphore.wait()
    return status
}

let input = FileHandle.standardInput.readDataToEndOfFile()
guard
    let object = try? JSONSerialization.jsonObject(with: input) as? [String: Any],
    let urlString = object["url"] as? String,
    let pageURL = URL(string: urlString),
    let requestId = object["request_id"] as? String,
    let nonce = object["nonce"] as? String,
    let volume = object["volume"] as? String
else {
    die("ntfsmnt-approve: missing url, request_id, nonce, or volume", 2)
}

let actions = (object["actions"] as? [String]) ?? []
let context = LAContext()
var authError: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &authError) else {
    die("ntfsmnt-approve: Touch ID unavailable", 2)
}

let semaphore = DispatchSemaphore(value: 0)
var approved = false
let reason = "Approve \(actions.joined(separator: ", ")) for \(volume)?"
context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { ok, _ in
    approved = ok
    semaphore.signal()
}
semaphore.wait()

guard var components = URLComponents(url: pageURL, resolvingAgainstBaseURL: false),
      let scheme = components.scheme, let host = components.host
else {
    die("ntfsmnt-approve: bad approval URL", 2)
}
let origin = "\(scheme)://\(host)" + (components.port.map { ":\($0)" } ?? "")
components.path = "/api/v1/grants/\(requestId)/" + (approved ? "approve" : "deny")
components.query = nil
guard let endpoint = components.url else {
    die("ntfsmnt-approve: bad approval URL", 2)
}

guard post(endpoint, nonce: nonce, origin: origin) == 200 else {
    die("ntfsmnt-approve: the approval request failed", 2)
}
exit(approved ? 0 : 3)
