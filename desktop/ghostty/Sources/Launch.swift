import Foundation

struct Launch {
    let command: String
    let workingDirectory: String
    let configPath: String?
    let tracePath: String?

    static func fromEnvironment() -> Launch {
        let environment = ProcessInfo.processInfo.environment
        let arguments = CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-psn") && !$0.hasPrefix("-NS") }
        return Launch(
            command: loginShellCommand(program: gpythonProgram(environment), arguments: Array(arguments)),
            workingDirectory: environment["GPYTHON_DESKTOP_CWD"] ?? startingDirectory(),
            configPath: Bundle.main.path(forResource: "ghostty", ofType: "conf"),
            tracePath: environment["GPYTHON_DESKTOP_TRACE"]
        )
    }

    private static func gpythonProgram(_ environment: [String: String]) -> String {
        if let chosen = environment["GPYTHON_DESKTOP_PROGRAM"] {
            return chosen
        }
        if let recorded = Bundle.main.path(forResource: "gpython-program", ofType: "txt"),
           let text = try? String(contentsOfFile: recorded, encoding: .utf8) {
            return text.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return "gpython"
    }

    private static func startingDirectory() -> String {
        let current = FileManager.default.currentDirectoryPath
        return current == "/" ? NSHomeDirectory() : current
    }

    private static func loginShellCommand(program: String, arguments: [String]) -> String {
        let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
        let words = ([program] + arguments).map(shellQuoted).joined(separator: " ")
        return "\(shell) -l -c \(shellQuoted("exec " + words))"
    }

    private static func shellQuoted(_ word: String) -> String {
        "'" + word.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
