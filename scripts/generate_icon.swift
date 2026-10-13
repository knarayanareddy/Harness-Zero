import Cocoa

func createIconImage(size: CGFloat) -> NSImage {
    let img = NSImage(size: NSSize(width: size, height: size))
    img.lockFocus()

    let bounds = NSRect(x: 0, y: 0, width: size, height: size)
    let ctx = NSGraphicsContext.current!.cgContext

    // Rounded Squircle
    let cornerRadius = size * 0.22
    let path = NSBezierPath(roundedRect: bounds, xRadius: cornerRadius, yRadius: cornerRadius)
    path.addClip()

    // Background Gradient (Dark Slate to Navy)
    let colorSpace = CGColorSpaceCreateDeviceRGB()
    let bgColors = [
        NSColor(red: 9/255, green: 13/255, blue: 22/255, alpha: 1.0).cgColor,
        NSColor(red: 15/255, green: 30/255, blue: 58/255, alpha: 1.0).cgColor
    ] as CFArray
    let bgGradient = CGGradient(colorsSpace: colorSpace, colors: bgColors, locations: [0.0, 1.0])!
    ctx.drawLinearGradient(bgGradient, start: CGPoint(x: 0, y: size), end: CGPoint(x: size, y: 0), options: [])

    // Glowing Concentric Ring (The "Harness" Event Horizon)
    let center = CGPoint(x: size / 2, y: size / 2)
    let ringRadius = size * 0.28
    ctx.setLineWidth(size * 0.05)
    ctx.setStrokeColor(NSColor(red: 56/255, green: 189/255, blue: 248/255, alpha: 0.9).cgColor) // Neon Cyan
    ctx.setShadow(offset: .zero, blur: size * 0.08, color: NSColor(red: 56/255, green: 189/255, blue: 248/255, alpha: 0.8).cgColor)
    ctx.strokeEllipse(in: CGRect(x: center.x - ringRadius, y: center.y - ringRadius, width: ringRadius * 2, height: ringRadius * 2))

    // Inner Core Orb
    let coreRadius = size * 0.12
    ctx.setFillColor(NSColor(red: 168/255, green: 85/255, blue: 247/255, alpha: 1.0).cgColor) // Violet
    ctx.setShadow(offset: .zero, blur: size * 0.06, color: NSColor(red: 168/255, green: 85/255, blue: 247/255, alpha: 0.9).cgColor)
    ctx.fillEllipse(in: CGRect(x: center.x - coreRadius, y: center.y - coreRadius, width: coreRadius * 2, height: coreRadius * 2))

    // Central "0" Symbol
    let text = "0" as NSString
    let font = NSFont.systemFont(ofSize: size * 0.18, weight: .black)
    let attrs: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: NSColor.white
    ]
    let textSize = text.size(withAttributes: attrs)
    let textRect = CGRect(x: center.x - textSize.width / 2, y: center.y - textSize.height / 2, width: textSize.width, height: textSize.height)
    text.draw(in: textRect, withAttributes: attrs)

    img.unlockFocus()
    return img
}

let fileManager = FileManager.default
let iconsetDir = "AppIcon.iconset"
try? fileManager.removeItem(atPath: iconsetDir)
try! fileManager.createDirectory(atPath: iconsetDir, withIntermediateDirectories: true)

let sizes: [(String, CGFloat)] = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024)
]

for (name, s) in sizes {
    let img = createIconImage(size: s)
    if let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) {
        let path = "\(iconsetDir)/\(name)"
        try! png.write(to: URL(fileURLWithPath: path))
    }
}

print("Iconset created successfully.")
