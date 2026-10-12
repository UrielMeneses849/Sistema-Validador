// OCR sin red, opcional en macOS. Protocolo JSONL por stdin/stdout.
import Foundation
import Vision

func emit(_ payload: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: payload),
       let text = String(data: data, encoding: .utf8) {
        print(text)
        fflush(stdout)
    }
}

emit(["ready": true])
while let line = readLine() {
    do {
        guard let json = line.data(using: .utf8),
              let input = try JSONSerialization.jsonObject(with: json) as? [String: Any] else {
            throw NSError(domain: "OCR", code: 1)
        }
        if input["command"] as? String == "shutdown" { break }
        guard let encoded = input["image_base64"] as? String,
              let data = Data(base64Encoded: encoded) else {
            throw NSError(domain: "OCR", code: 2)
        }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        request.recognitionLanguages = ["es-ES", "en-US"]
        request.minimumTextHeight = 0.04
        try VNImageRequestHandler(data: data, options: [:]).perform([request])
        let readings = (request.results ?? []).compactMap { $0.topCandidates(1).first }
        let observations: [[String: Any]] = (request.results ?? []).compactMap { observation in
            guard let text = observation.topCandidates(1).first else { return nil }
            let b = observation.boundingBox
            return ["text": text.string, "confidence": Double(text.confidence),
                    "x": b.minX, "y": 1-b.maxY, "width": b.width, "height": b.height]
        }
        emit(["text": readings.map { $0.string }.joined(separator: "\n"),
              "observations": observations,
              "confidence": readings.isEmpty ? 0 : readings.map { Double($0.confidence) }.reduce(0,+) / Double(readings.count)])
    } catch {
        emit(["error": String(describing: error)])
    }
}
