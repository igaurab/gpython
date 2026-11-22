import AppKit
import GhosttyKit

let processStart = Date()

if ghostty_init(UInt(CommandLine.argc), CommandLine.unsafeArgv) != GHOSTTY_SUCCESS {
    FileHandle.standardError.write(Data("gpython: libghostty failed to initialise\n".utf8))
    exit(1)
}

let application = NSApplication.shared
let delegate = AppDelegate(launch: Launch.fromEnvironment(), startedAt: processStart)
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
