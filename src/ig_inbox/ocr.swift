// ocr — macOS Vision text recognition CLI.
// Usage: ocr <image> [image ...]   → recognized lines to stdout (per-image blocks)
// Built by setup (macOS only): xcrun swiftc -O ocr.swift -o bin/ocr

import Foundation
import Vision
import AppKit

func recognize(path: String) -> [String] {
    guard let image = NSImage(contentsOfFile: path),
          let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let cgImage = rep.cgImage else {
        FileHandle.standardError.write("ocr: cannot load \(path)\n".data(using: .utf8)!)
        return []
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        FileHandle.standardError.write("ocr: vision failed on \(path): \(error)\n".data(using: .utf8)!)
        return []
    }
    guard let observations = request.results else { return [] }
    return observations.compactMap { $0.topCandidates(1).first?.string }
}

let args = Array(CommandLine.arguments.dropFirst())
if args.isEmpty {
    FileHandle.standardError.write("usage: ocr <image> [image ...]\n".data(using: .utf8)!)
    exit(64)
}
for path in args {
    for line in recognize(path: path) {
        print(line)
    }
}
