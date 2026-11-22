import AppKit
import GhosttyKit

final class Trace {
    private let logPath: String
    private let commandPath: String
    private let startedAt: Date
    private weak var terminal: TerminalView?
    private var firstTextSeen = false
    private var firstFrameSeen = false
    private var timer: Timer?
    private var pendingLatency: (sentAt: Date, before: String, label: String)?

    init(path: String, startedAt: Date, terminal: TerminalView) {
        self.logPath = path
        self.commandPath = path + ".cmd"
        self.startedAt = startedAt
        self.terminal = terminal
        FileManager.default.createFile(atPath: path, contents: nil)
        log("window_ms \(milliseconds(since: startedAt))")
        let timer = Timer(timeInterval: 0.002, repeats: true) { [weak self] _ in self?.poll() }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func poll() {
        guard let terminal else { return }
        if !firstFrameSeen {
            let text = terminal.screenText()
            if !firstTextSeen, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                firstTextSeen = true
                log("first_text_ms \(milliseconds(since: startedAt))")
            }
            if text.contains("gpython") {
                firstFrameSeen = true
                log("first_gpython_frame_ms \(milliseconds(since: startedAt))")
            }
        }
        if let pending = pendingLatency, terminal.screenText() != pending.before {
            log("latency_ms \(pending.label) \(milliseconds(since: pending.sentAt))")
            pendingLatency = nil
        }
        if pendingLatency == nil {
            runNextCommand(on: terminal)
        }
    }

    private func runNextCommand(on terminal: TerminalView) {
        guard let script = try? String(contentsOfFile: commandPath, encoding: .utf8) else { return }
        var lines = script.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        guard !lines.isEmpty else {
            try? FileManager.default.removeItem(atPath: commandPath)
            return
        }
        let line = lines.removeFirst()
        if lines.isEmpty {
            try? FileManager.default.removeItem(atPath: commandPath)
        } else {
            try? lines.joined(separator: "\n").write(toFile: commandPath, atomically: true, encoding: .utf8)
        }
        run(line.split(separator: " ", maxSplits: 1).map(String.init), on: terminal)
    }

    private func run(_ words: [String], on terminal: TerminalView) {
        let argument = words.count > 1 ? words[1] : ""
        switch words.first {
        case "dump":
            try? terminal.screenText().write(toFile: argument, atomically: true, encoding: .utf8)
            log("dumped \(argument)")
        case "size":
            let size = terminal.gridSize()
            log("grid \(size.columns)x\(size.rows) cell \(size.cell_width_px)x\(size.cell_height_px) px \(size.width_px)x\(size.height_px)")
        case "resize":
            let parts = argument.split(separator: " ").compactMap { Double($0) }
            if parts.count == 2 { terminal.window?.setContentSize(NSSize(width: parts[0], height: parts[1])) }
        case "key":
            press(argument, on: terminal)
        case "timedkey":
            pendingLatency = (Date(), terminal.screenText(), argument)
            press(argument, on: terminal)
        case "click":
            let parts = argument.split(separator: " ").compactMap { Double($0) }
            if parts.count == 2 { click(x: parts[0], y: parts[1], on: terminal) }
        case "scroll":
            scroll(lines: Int32(argument) ?? 0, on: terminal)
        case "windowid":
            log("windowid \(terminal.window?.windowNumber ?? 0)")
        default:
            log("unknown \(words)")
        }
    }

    private func press(_ description: String, on terminal: TerminalView) {
        let parts = description.split(separator: " ").map(String.init)
        guard let keyCode = parts.first.flatMap({ UInt16($0) }) else { return }
        let characters = parts.count > 1 ? decoded(parts[1]) : ""
        let flags = modifierFlags(parts.count > 2 ? parts[2] : "")
        for type in [NSEvent.EventType.keyDown, .keyUp] {
            guard let event = NSEvent.keyEvent(
                with: type,
                location: .zero,
                modifierFlags: flags,
                timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: terminal.window?.windowNumber ?? 0,
                context: nil,
                characters: characters,
                charactersIgnoringModifiers: characters,
                isARepeat: false,
                keyCode: keyCode
            ) else { continue }
            if type == .keyDown, terminal.performKeyEquivalent(with: event) { continue }
            if type == .keyDown { terminal.keyDown(with: event) } else { terminal.keyUp(with: event) }
        }
    }

    private func decoded(_ word: String) -> String {
        if word.hasPrefix("u+"), let value = UInt32(word.dropFirst(2), radix: 16), let scalar = Unicode.Scalar(value) {
            return String(Character(scalar))
        }
        return word
    }

    private func modifierFlags(_ names: String) -> NSEvent.ModifierFlags {
        var flags: NSEvent.ModifierFlags = []
        if names.contains("ctrl") { flags.insert(.control) }
        if names.contains("alt") { flags.insert(.option) }
        if names.contains("shift") { flags.insert(.shift) }
        if names.contains("cmd") { flags.insert(.command) }
        return flags
    }

    private func click(x: Double, y: Double, on terminal: TerminalView) {
        let inView = NSPoint(x: x, y: Double(terminal.frame.height) - y)
        let inWindow = terminal.convert(inView, to: nil)
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            guard let event = NSEvent.mouseEvent(
                with: type,
                location: inWindow,
                modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime,
                windowNumber: terminal.window?.windowNumber ?? 0,
                context: nil,
                eventNumber: 0,
                clickCount: 1,
                pressure: 1
            ) else { continue }
            if type == .leftMouseDown { terminal.mouseDown(with: event) } else { terminal.mouseUp(with: event) }
        }
    }

    private func scroll(lines: Int32, on terminal: TerminalView) {
        guard let source = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 1, wheel1: lines, wheel2: 0, wheel3: 0),
              let event = NSEvent(cgEvent: source) else { return }
        log("scroll deltaY \(event.scrollingDeltaY) precise \(event.hasPreciseScrollingDeltas)")
        terminal.scrollWheel(with: event)
    }

    private func milliseconds(since date: Date) -> String {
        String(format: "%.1f", Date().timeIntervalSince(date) * 1000)
    }

    private func log(_ line: String) {
        guard let handle = FileHandle(forWritingAtPath: logPath) else { return }
        handle.seekToEndOfFile()
        handle.write(Data((line + "\n").utf8))
        handle.closeFile()
    }
}
