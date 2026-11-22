import AppKit
import GhosttyKit

final class GhosttyRuntime {
    private(set) var app: ghostty_app_t?
    private var config: ghostty_config_t?
    var onTitle: ((String) -> Void)?
    var onChildExited: (() -> Void)?

    init?(configPath: String?) {
        guard let config = ghostty_config_new() else { return nil }
        if let configPath {
            ghostty_config_load_file(config, configPath)
        }
        ghostty_config_finalize(config)
        self.config = config

        var runtime = ghostty_runtime_config_s(
            userdata: Unmanaged.passUnretained(self).toOpaque(),
            supports_selection_clipboard: false,
            wakeup_cb: { userdata in GhosttyRuntime.from(userdata)?.scheduleTick() },
            action_cb: { app, target, action in GhosttyRuntime.perform(app: app, target: target, action: action) },
            read_clipboard_cb: { userdata, _, state, mimes, count, _ in
                TerminalView.from(userdata)?.answerClipboardRead(state: state, mimes: mimes, count: count)
                    ?? GHOSTTY_CLIPBOARD_READ_UNSUPPORTED
            },
            confirm_read_clipboard_cb: { userdata, confirm, state, _ in
                TerminalView.from(userdata)?.approveClipboardRead(confirm: confirm, state: state)
            },
            write_clipboard_cb: { _, _, contents, count, _ in
                Clipboard.write(contents: contents, count: count)
            },
            close_surface_cb: { userdata, _ in
                DispatchQueue.main.async { TerminalView.from(userdata)?.runtime.onChildExited?() }
            }
        )
        guard let app = ghostty_app_new(&runtime, config) else { return nil }
        self.app = app
    }

    deinit {
        if let app { ghostty_app_free(app) }
        if let config { ghostty_config_free(config) }
    }

    func setFocus(_ focused: Bool) {
        if let app { ghostty_app_set_focus(app, focused) }
    }

    private func scheduleTick() {
        DispatchQueue.main.async { [weak self] in
            if let app = self?.app { ghostty_app_tick(app) }
        }
    }

    private static func from(_ userdata: UnsafeMutableRawPointer?) -> GhosttyRuntime? {
        userdata.map { Unmanaged<GhosttyRuntime>.fromOpaque($0).takeUnretainedValue() }
    }

    private static func perform(app: ghostty_app_t?, target: ghostty_target_s, action: ghostty_action_s) -> Bool {
        guard let app, let runtime = from(ghostty_app_userdata(app)) else { return false }
        switch action.tag {
        case GHOSTTY_ACTION_SET_TITLE:
            let title = String(cString: action.action.set_title.title)
            DispatchQueue.main.async { runtime.onTitle?(title) }
            return true
        case GHOSTTY_ACTION_SHOW_CHILD_EXITED, GHOSTTY_ACTION_QUIT, GHOSTTY_ACTION_CLOSE_WINDOW:
            DispatchQueue.main.async { runtime.onChildExited?() }
            return true
        case GHOSTTY_ACTION_OPEN_URL:
            let link = action.action.open_url
            let text = String(decoding: UnsafeRawBufferPointer(start: link.url, count: Int(link.len)), as: UTF8.self)
            if let url = URL(string: text) {
                DispatchQueue.main.async { NSWorkspace.shared.open(url) }
            }
            return true
        case GHOSTTY_ACTION_RING_BELL:
            DispatchQueue.main.async { NSSound.beep() }
            return true
        default:
            return false
        }
    }
}

enum Clipboard {
    static let plainText = "text/plain"

    static func write(contents: UnsafePointer<ghostty_clipboard_content_s>?, count: Int) {
        guard let contents else { return }
        for index in 0..<count where String(cString: contents[index].mime) == plainText {
            let bytes = UnsafeRawBufferPointer(start: contents[index].data, count: contents[index].len)
            let text = String(decoding: bytes, as: UTF8.self)
            DispatchQueue.main.async {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            }
        }
    }

    static func complete(surface: ghostty_surface_t, state: UnsafeMutableRawPointer, text: String) {
        plainText.withCString { mime in
            text.withCString { data in
                var content = ghostty_clipboard_content_s(mime: mime, data: data, len: text.utf8.count)
                withUnsafePointer(to: &content) { contentPointer in
                    var complete = ghostty_clipboard_complete_s(
                        contents: contentPointer,
                        contents_len: 1,
                        available: nil,
                        available_len: 0,
                        confirmed: true,
                        remember: false
                    )
                    ghostty_surface_complete_clipboard_request(surface, &complete, state)
                }
            }
        }
    }
}
