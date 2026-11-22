import AppKit

func placeholderIcon(pixels: Int) -> Data? {
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4,
        hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    ) else { return nil }
    let side = CGFloat(pixels)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let tile = NSRect(x: 0, y: 0, width: side, height: side).insetBy(dx: side * 0.09, dy: side * 0.09)
    let shape = NSBezierPath(roundedRect: tile, xRadius: side * 0.19, yRadius: side * 0.19)
    NSGradient(
        starting: NSColor(red: 0.13, green: 0.16, blue: 0.24, alpha: 1),
        ending: NSColor(red: 0.05, green: 0.06, blue: 0.10, alpha: 1)
    )?.draw(in: shape, angle: -90)
    let prompt = NSAttributedString(string: "g›", attributes: [
        .font: NSFont.monospacedSystemFont(ofSize: side * 0.44, weight: .bold),
        .foregroundColor: NSColor(red: 0.45, green: 0.86, blue: 0.62, alpha: 1),
    ])
    let size = prompt.size()
    prompt.draw(at: NSPoint(x: (side - size.width) / 2, y: (side - size.height) / 2 + side * 0.02))
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])
}

let iconset = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)
for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let name = scale == 1 ? "icon_\(points)x\(points).png" : "icon_\(points)x\(points)@2x.png"
        try placeholderIcon(pixels: points * scale)?.write(to: iconset.appendingPathComponent(name))
    }
}
