// Renders C-Trace eval bills (Bangla, mixed and English) with CoreText, which shapes Bangla script correctly.
// Used by scripts/make_eval_bills.py; macOS only. Input: a JSON file with a list of jobs:
//   {"out": "path", "format": "png" | "pdf", "width": 1240, "height": 1754,
//    "lines": [{"text": "...", "size": 26, "bold": false, "gap": 10}]}
// PNG output is a raster (like a scan or photo); PDF output has a real text layer.
import AppKit
import Foundation

struct Line: Decodable { let text: String; let size: Double; let bold: Bool?; let gap: Double? }
struct Job: Decodable { let out: String; let format: String; let width: Double; let height: Double; let lines: [Line] }

func font(_ size: Double, _ bold: Bool) -> NSFont {
    let name = bold ? "Kohinoor Bangla Semibold" : "Kohinoor Bangla"
    return NSFont(name: name, size: size) ?? NSFont.systemFont(ofSize: size, weight: bold ? .semibold : .regular)
}

func draw(_ job: Job) {
    var y = job.height - 70.0
    for line in job.lines {
        let attrs: [NSAttributedString.Key: Any] = [.font: font(line.size, line.bold ?? false), .foregroundColor: NSColor.black]
        let s = NSAttributedString(string: line.text, attributes: attrs)
        let h = s.size().height
        y -= h
        s.draw(at: NSPoint(x: 70, y: y))
        y -= line.gap ?? 10
    }
}

let args = CommandLine.arguments
guard args.count == 2, let data = FileManager.default.contents(atPath: args[1]) else {
    FileHandle.standardError.write("usage: swift render_bill.swift jobs.json\n".data(using: .utf8)!)
    exit(2)
}
let jobs = try JSONDecoder().decode([Job].self, from: data)
for job in jobs {
    let size = NSSize(width: job.width, height: job.height)
    if job.format == "pdf" {
        var box = CGRect(origin: .zero, size: size)
        let url = URL(fileURLWithPath: job.out) as CFURL
        guard let ctx = CGContext(url, mediaBox: &box, nil) else { fatalError("cannot create \(job.out)") }
        ctx.beginPDFPage(nil)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
        draw(job)
        NSGraphicsContext.restoreGraphicsState()
        ctx.endPDFPage()
        ctx.closePDF()
    } else {
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(job.width), pixelsHigh: Int(job.height),
                                   bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                   colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        NSColor.white.setFill()
        NSRect(origin: .zero, size: size).fill()
        draw(job)
        NSGraphicsContext.restoreGraphicsState()
        try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: job.out))
    }
}
print("rendered \(jobs.count)")
