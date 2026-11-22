import AppKit
import GhosttyKit

final class TerminalView: NSView {
    let runtime: GhosttyRuntime
    private(set) var surface: ghostty_surface_t?
    private var trackingArea: NSTrackingArea?

    init(runtime: GhosttyRuntime, launch: Launch, frame: NSRect) {
        self.runtime = runtime
        super.init(frame: frame)
        surface = makeSurface(launch: launch)
        refreshTrackingArea()
    }

    required init?(coder: NSCoder) {
        nil
    }

    static func from(_ userdata: UnsafeMutableRawPointer?) -> TerminalView? {
        userdata.map { Unmanaged<TerminalView>.fromOpaque($0).takeUnretainedValue() }
    }

    func closeSurface() {
        if let surface { ghostty_surface_free(surface) }
        surface = nil
    }

    func screenText() -> String {
        guard let surface else { return "" }
        let corner = { (coord: ghostty_point_coord_e) in
            ghostty_point_s(tag: GHOSTTY_POINT_VIEWPORT, coord: coord, x: 0, y: 0)
        }
        let whole = ghostty_selection_s(
            top_left: corner(GHOSTTY_POINT_COORD_TOP_LEFT),
            bottom_right: corner(GHOSTTY_POINT_COORD_BOTTOM_RIGHT),
            rectangle: false
        )
        var text = ghostty_text_s()
        guard ghostty_surface_read_text(surface, whole, &text) else { return "" }
        defer { ghostty_surface_free_text(surface, &text) }
        return String(decoding: UnsafeRawBufferPointer(start: text.text, count: Int(text.text_len)), as: UTF8.self)
    }

    func gridSize() -> ghostty_surface_size_s {
        surface.map(ghostty_surface_size) ?? ghostty_surface_size_s()
    }

    private func makeSurface(launch: Launch) -> ghostty_surface_t? {
        guard let app = runtime.app else { return nil }
        var config = ghostty_surface_config_new()
        config.platform_tag = GHOSTTY_PLATFORM_MACOS
        config.platform = ghostty_platform_u(macos: ghostty_platform_macos_s(nsview: Unmanaged.passUnretained(self).toOpaque()))
        config.userdata = Unmanaged.passUnretained(self).toOpaque()
        config.scale_factor = Double(NSScreen.main?.backingScaleFactor ?? 2)
        config.wait_after_command = false
        return launch.command.withCString { command in
            launch.workingDirectory.withCString { directory in
                config.command = command
                config.working_directory = directory
                return ghostty_surface_new(app, &config)
            }
        }
    }

    override var acceptsFirstResponder: Bool { true }

    override func becomeFirstResponder() -> Bool {
        setFocus(true)
        return true
    }

    override func resignFirstResponder() -> Bool {
        setFocus(false)
        return true
    }

    func setFocus(_ focused: Bool) {
        if let surface { ghostty_surface_set_focus(surface, focused) }
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        followScreen()
        viewDidChangeBackingProperties()
    }

    func followScreen() {
        guard let surface, let screen = window?.screen else { return }
        let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber
        ghostty_surface_set_display_id(surface, number?.uint32Value ?? 0)
    }

    override func viewDidChangeBackingProperties() {
        super.viewDidChangeBackingProperties()
        guard let surface, let window else { return }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        layer?.contentsScale = window.backingScaleFactor
        CATransaction.commit()
        let scaled = convertToBacking(frame)
        let horizontal = frame.width > 0 ? scaled.width / frame.width : window.backingScaleFactor
        let vertical = frame.height > 0 ? scaled.height / frame.height : window.backingScaleFactor
        ghostty_surface_set_content_scale(surface, horizontal, vertical)
        reportSize()
    }

    override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        reportSize()
    }

    private func reportSize() {
        guard let surface else { return }
        let scaled = convertToBacking(bounds.size)
        guard scaled.width > 0, scaled.height > 0 else { return }
        ghostty_surface_set_size(surface, UInt32(scaled.width), UInt32(scaled.height))
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        refreshTrackingArea()
    }

    private func refreshTrackingArea() {
        if let trackingArea { removeTrackingArea(trackingArea) }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.mouseEnteredAndExited, .mouseMoved, .inVisibleRect, .activeAlways],
            owner: self
        )
        addTrackingArea(area)
        trackingArea = area
    }

    override func keyDown(with event: NSEvent) {
        sendKey(event.isARepeat ? GHOSTTY_ACTION_REPEAT : GHOSTTY_ACTION_PRESS, event: event, withText: true)
    }

    override func keyUp(with event: NSEvent) {
        sendKey(GHOSTTY_ACTION_RELEASE, event: event, withText: false)
    }

    override func flagsChanged(with event: NSEvent) {
        guard let modifier = Keys.modifier(forKeyCode: event.keyCode) else { return }
        let held = Keys.mods(event.modifierFlags).rawValue & modifier.rawValue != 0
        sendKey(held ? GHOSTTY_ACTION_PRESS : GHOSTTY_ACTION_RELEASE, event: event, withText: false)
    }

    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        guard event.type == .keyDown, window?.firstResponder === self else { return false }
        let flags = event.modifierFlags
        guard flags.contains(.control), !flags.contains(.command) else { return false }
        keyDown(with: event)
        return true
    }

    override func doCommand(by selector: Selector) {}

    private func sendKey(_ action: ghostty_input_action_e, event: NSEvent, withText: Bool) {
        guard let surface else { return }
        let translation = Keys.flags(ghostty_surface_key_translation_mods(surface, Keys.mods(event.modifierFlags)), over: event.modifierFlags)
        var key = ghostty_input_key_s()
        key.action = action
        key.keycode = UInt32(event.keyCode)
        key.mods = Keys.mods(event.modifierFlags)
        key.consumed_mods = Keys.mods(translation.subtracting([.control, .command]))
        key.composing = false
        key.unshifted_codepoint = 0
        if event.type == .keyDown || event.type == .keyUp {
            key.unshifted_codepoint = event.characters(byApplyingModifiers: [])?.unicodeScalars.first?.value ?? 0
        }
        let text = withText ? Keys.printableText(event, translation: translation) : nil
        if let text {
            text.withCString { pointer in
                key.text = pointer
                _ = ghostty_surface_key(surface, key)
            }
        } else {
            _ = ghostty_surface_key(surface, key)
        }
    }

    @objc func copy(_ sender: Any?) {
        performBinding("copy_to_clipboard")
    }

    @objc func paste(_ sender: Any?) {
        performBinding("paste_from_clipboard")
    }

    @objc override func selectAll(_ sender: Any?) {
        performBinding("select_all")
    }

    private func performBinding(_ name: String) {
        guard let surface else { return }
        _ = ghostty_surface_binding_action(surface, name, UInt(name.utf8.count))
    }

    func answerClipboardRead(
        state: UnsafeMutableRawPointer,
        mimes: UnsafePointer<UnsafePointer<CChar>>,
        count: Int
    ) -> ghostty_clipboard_read_result_e {
        guard let surface else { return GHOSTTY_CLIPBOARD_READ_UNSUPPORTED }
        let wantsText = (0..<count).contains { String(cString: mimes[$0]) == Clipboard.plainText }
        guard wantsText, let text = NSPasteboard.general.string(forType: .string) else {
            return GHOSTTY_CLIPBOARD_READ_UNAVAILABLE
        }
        Clipboard.complete(surface: surface, state: state, text: text)
        return GHOSTTY_CLIPBOARD_READ_STARTED
    }

    func approveClipboardRead(confirm: UnsafePointer<ghostty_clipboard_confirm_s>?, state: UnsafeMutableRawPointer) {
        guard let surface, let confirm else { return }
        var complete = ghostty_clipboard_complete_s(
            contents: confirm.pointee.contents,
            contents_len: confirm.pointee.contents_len,
            available: confirm.pointee.available,
            available_len: confirm.pointee.available_len,
            confirmed: true,
            remember: false
        )
        ghostty_surface_complete_clipboard_request(surface, &complete, state)
    }

    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(self)
        sendButton(GHOSTTY_MOUSE_PRESS, GHOSTTY_MOUSE_LEFT, event)
    }

    override func mouseUp(with event: NSEvent) {
        sendButton(GHOSTTY_MOUSE_RELEASE, GHOSTTY_MOUSE_LEFT, event)
    }

    override func rightMouseDown(with event: NSEvent) {
        sendButton(GHOSTTY_MOUSE_PRESS, GHOSTTY_MOUSE_RIGHT, event)
    }

    override func rightMouseUp(with event: NSEvent) {
        sendButton(GHOSTTY_MOUSE_RELEASE, GHOSTTY_MOUSE_RIGHT, event)
    }

    override func otherMouseDown(with event: NSEvent) {
        sendButton(GHOSTTY_MOUSE_PRESS, GHOSTTY_MOUSE_MIDDLE, event)
    }

    override func otherMouseUp(with event: NSEvent) {
        sendButton(GHOSTTY_MOUSE_RELEASE, GHOSTTY_MOUSE_MIDDLE, event)
    }

    override func mouseMoved(with event: NSEvent) {
        sendPosition(event)
    }

    override func mouseDragged(with event: NSEvent) {
        sendPosition(event)
    }

    override func rightMouseDragged(with event: NSEvent) {
        sendPosition(event)
    }

    override func otherMouseDragged(with event: NSEvent) {
        sendPosition(event)
    }

    override func mouseExited(with event: NSEvent) {
        guard let surface, NSEvent.pressedMouseButtons == 0 else { return }
        ghostty_surface_mouse_pos(surface, -1, -1, Keys.mods(event.modifierFlags))
    }

    override func scrollWheel(with event: NSEvent) {
        guard let surface else { return }
        let precise = event.hasPreciseScrollingDeltas
        let speed: Double = precise ? 2 : 1
        let mods = (precise ? 1 : 0) | (Keys.momentum(event.momentumPhase) << 1)
        ghostty_surface_mouse_scroll(surface, event.scrollingDeltaX * speed, event.scrollingDeltaY * speed, mods)
    }

    private func sendButton(_ state: ghostty_input_mouse_state_e, _ button: ghostty_input_mouse_button_e, _ event: NSEvent) {
        guard let surface else { return }
        sendPosition(event)
        _ = ghostty_surface_mouse_button(surface, state, button, Keys.mods(event.modifierFlags))
    }

    private func sendPosition(_ event: NSEvent) {
        guard let surface else { return }
        let position = convert(event.locationInWindow, from: nil)
        ghostty_surface_mouse_pos(surface, position.x, frame.height - position.y, Keys.mods(event.modifierFlags))
    }
}

enum Keys {
    static func mods(_ flags: NSEvent.ModifierFlags) -> ghostty_input_mods_e {
        var mods = GHOSTTY_MODS_NONE.rawValue
        if flags.contains(.shift) { mods |= GHOSTTY_MODS_SHIFT.rawValue }
        if flags.contains(.control) { mods |= GHOSTTY_MODS_CTRL.rawValue }
        if flags.contains(.option) { mods |= GHOSTTY_MODS_ALT.rawValue }
        if flags.contains(.command) { mods |= GHOSTTY_MODS_SUPER.rawValue }
        if flags.contains(.capsLock) { mods |= GHOSTTY_MODS_CAPS.rawValue }
        return ghostty_input_mods_e(mods)
    }

    static func flags(_ mods: ghostty_input_mods_e, over original: NSEvent.ModifierFlags) -> NSEvent.ModifierFlags {
        var flags = original
        let pairs: [(ghostty_input_mods_e, NSEvent.ModifierFlags)] = [
            (GHOSTTY_MODS_SHIFT, .shift), (GHOSTTY_MODS_CTRL, .control),
            (GHOSTTY_MODS_ALT, .option), (GHOSTTY_MODS_SUPER, .command),
        ]
        for (mod, flag) in pairs {
            if mods.rawValue & mod.rawValue != 0 { flags.insert(flag) } else { flags.remove(flag) }
        }
        return flags
    }

    static func modifier(forKeyCode keyCode: UInt16) -> ghostty_input_mods_e? {
        switch keyCode {
        case 0x39: return GHOSTTY_MODS_CAPS
        case 0x38, 0x3C: return GHOSTTY_MODS_SHIFT
        case 0x3B, 0x3E: return GHOSTTY_MODS_CTRL
        case 0x3A, 0x3D: return GHOSTTY_MODS_ALT
        case 0x37, 0x36: return GHOSTTY_MODS_SUPER
        default: return nil
        }
    }

    static func printableText(_ event: NSEvent, translation: NSEvent.ModifierFlags) -> String? {
        guard let characters = event.characters(byApplyingModifiers: translation), !characters.isEmpty else { return nil }
        guard characters.count == 1, let scalar = characters.unicodeScalars.first else { return characters }
        if scalar.value < 0x20 {
            let plain = event.characters(byApplyingModifiers: translation.subtracting(.control))
            return plain.flatMap { ($0.unicodeScalars.first?.value ?? 0) >= 0x20 ? $0 : nil }
        }
        if scalar.value == 0x7F || (0xF700...0xF8FF).contains(scalar.value) { return nil }
        return characters
    }

    static func momentum(_ phase: NSEvent.Phase) -> Int32 {
        switch phase {
        case .began: return 1
        case .stationary: return 2
        case .changed: return 3
        case .ended: return 4
        case .cancelled: return 5
        case .mayBegin: return 6
        default: return 0
        }
    }
}
