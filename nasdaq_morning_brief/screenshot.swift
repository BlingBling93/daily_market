import AppKit
import WebKit

final class ScreenshotDelegate: NSObject, WKNavigationDelegate {
    private let webView: WKWebView
    private let outputURL: URL
    private let width: CGFloat
    private let height: CGFloat
    private var finished = false

    init(webView: WKWebView, outputURL: URL, width: CGFloat, height: CGFloat) {
        self.webView = webView
        self.outputURL = outputURL
        self.width = width
        self.height = height
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // Let WebKit finish layout and font/image loading before measuring the card.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
            self.capture()
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        fail("Failed to load HTML: \(error)")
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        fail("Failed to load HTML: \(error)")
    }

    private func capture() {
        let script = """
        (() => {
          const card = document.querySelector('.card');
          if (!card) throw new Error('HTML does not contain a .card element');
          const r = card.getBoundingClientRect();
          return {x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height};
        })()
        """
        webView.evaluateJavaScript(script) { result, error in
            if let error = error {
                self.fail("Could not measure report card: \(error)")
                return
            }
            guard let rect = result as? [String: CGFloat],
                  let x = rect["x"], let y = rect["y"],
                  let cardWidth = rect["width"], let cardHeight = rect["height"],
                  cardWidth > 0, cardHeight > 0 else {
                self.fail("Report card has invalid dimensions")
                return
            }
            let snapshotRect = CGRect(x: x, y: y, width: cardWidth, height: cardHeight)
            let configuration = WKSnapshotConfiguration()
            configuration.rect = snapshotRect
            configuration.snapshotWidth = NSNumber(value: Double(self.width * 2))
            self.webView.takeSnapshot(with: configuration) { image, error in
                if let error = error {
                    self.fail("WebKit snapshot failed: \(error)")
                    return
                }
                guard let image = image,
                      let tiff = image.tiffRepresentation,
                      let bitmap = NSBitmapImageRep(data: tiff),
                      let png = bitmap.representation(using: .png, properties: [:]) else {
                    self.fail("WebKit could not encode the snapshot as PNG")
                    return
                }
                do {
                    try png.write(to: self.outputURL, options: .atomic)
                    self.finished = true
                    NSApp.terminate(nil)
                } catch {
                    self.fail("Could not write PNG: \(error)")
                }
            }
        }
    }

    private func fail(_ message: String) {
        guard !finished else { return }
        fputs(message + "\n", stderr)
        finished = true
        NSApp.terminate(nil)
    }

    func timeout() {
        fail("Timed out while rendering HTML with WebKit")
    }
}

func run() throws {
    guard CommandLine.arguments.count == 5 else {
        throw NSError(domain: "Screenshot", code: 1, userInfo: [NSLocalizedDescriptionKey: "Usage: screenshot.swift <inputHtml> <outputPng> <width> <height>"])
    }
    let inputURL = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
    let outputURL = URL(fileURLWithPath: CommandLine.arguments[2]).standardizedFileURL
    guard let width = Double(CommandLine.arguments[3]),
          let height = Double(CommandLine.arguments[4]), width > 0, height > 0 else {
        throw NSError(domain: "Screenshot", code: 2, userInfo: [NSLocalizedDescriptionKey: "Viewport dimensions must be positive numbers"])
    }

    NSApplication.shared.setActivationPolicy(.accessory)
    let configuration = WKWebViewConfiguration()
    configuration.websiteDataStore = .nonPersistent()
    let webView = WKWebView(frame: CGRect(x: 0, y: 0, width: width, height: height), configuration: configuration)
    let window = NSWindow(
        contentRect: CGRect(x: 0, y: 0, width: width, height: height),
        styleMask: .borderless,
        backing: .buffered,
        defer: false
    )
    window.alphaValue = 0.01
    window.contentView = webView
    window.makeKeyAndOrderFront(nil)
    let delegate = ScreenshotDelegate(webView: webView, outputURL: outputURL, width: width, height: height)
    webView.navigationDelegate = delegate
    webView.loadFileURL(inputURL, allowingReadAccessTo: inputURL.deletingLastPathComponent())
    _ = window
    DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
        delegate.timeout()
    }
    withExtendedLifetime(delegate) {
        NSApp.run()
    }
    guard FileManager.default.fileExists(atPath: outputURL.path),
          let attributes = try? FileManager.default.attributesOfItem(atPath: outputURL.path),
          let fileSize = attributes[.size] as? NSNumber, fileSize.intValue > 0 else {
        throw NSError(domain: "Screenshot", code: 3, userInfo: [NSLocalizedDescriptionKey: "WebKit exited without producing a PNG"])
    }
}

do {
    try run()
} catch {
    fputs("\(error)\n", stderr)
    exit(1)
}
