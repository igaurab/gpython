import AppKit
import GhosttyKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let launch: Launch
    private let startedAt: Date
    private var runtime: GhosttyRuntime?
    private var window: NSWindow?
    private var terminal: TerminalView?
    private var trace: Trace?

    init(launch: Launch, startedAt: Date) {
        self.launch = launch
        self.startedAt = startedAt
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = MainMenu.build()
        guard let runtime = GhosttyRuntime(configPath: launch.configPath) else {
            FileHandle.standardError.write(Data("gpython: could not create the terminal\n".utf8))
            NSApp.terminate(nil)
            return
        }
        self.runtime = runtime
        runtime.onChildExited = { NSApp.terminate(nil) }

        let frame = NSRect(x: 0, y: 0, width: 1100, height: 720)
        let window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        let terminal = TerminalView(runtime: runtime, launch: launch, frame: frame)
        runtime.onTitle = { [weak window] title in window?.title = title.isEmpty ? "gpython" : title }
        window.title = "gpython"
        window.contentView = terminal
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("gpython-main")
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(terminal)
        NSApp.activate(ignoringOtherApps: true)
        self.window = window
        self.terminal = terminal
        if let path = launch.tracePath {
            trace = Trace(path: path, startedAt: startedAt, terminal: terminal)
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        terminal?.closeSurface()
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        runtime?.setFocus(true)
    }

    func applicationDidResignActive(_ notification: Notification) {
        runtime?.setFocus(false)
    }

    func windowDidBecomeKey(_ notification: Notification) {
        terminal?.setFocus(true)
    }

    func windowDidResignKey(_ notification: Notification) {
        terminal?.setFocus(false)
    }

    func windowDidChangeScreen(_ notification: Notification) {
        terminal?.followScreen()
        terminal?.viewDidChangeBackingProperties()
    }
}

enum MainMenu {
    static func build() -> NSMenu {
        let bar = NSMenu()
        bar.addItem(submenu(title: "gpython", items: [
            item("About gpython", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), ""),
            .separator(),
            item("Hide gpython", #selector(NSApplication.hide(_:)), "h"),
            item("Quit gpython", #selector(NSApplication.terminate(_:)), "q"),
        ]))
        bar.addItem(submenu(title: "Edit", items: [
            item("Copy", #selector(TerminalView.copy(_:)), "c"),
            item("Paste", #selector(TerminalView.paste(_:)), "v"),
            item("Select All", #selector(TerminalView.selectAll(_:)), "a"),
        ]))
        bar.addItem(submenu(title: "Window", items: [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:)), ""),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ]))
        return bar
    }

    private static func submenu(title: String, items: [NSMenuItem]) -> NSMenuItem {
        let holder = NSMenuItem()
        let menu = NSMenu(title: title)
        items.forEach(menu.addItem)
        holder.submenu = menu
        return holder
    }

    private static func item(
        _ title: String,
        _ action: Selector,
        _ key: String,
        _ modifiers: NSEvent.ModifierFlags = .command
    ) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        return item
    }
}
