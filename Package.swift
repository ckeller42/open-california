// swift-tools-version: 6.0
import PackageDescription

// The C codec and the control planner are linked as they are (no copies): the same code the ESP32 satellite runs.
let package = Package(
    name: "CaliKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "CaliCore", targets: ["CaliCore"]),
        .library(name: "CaliBLE", targets: ["CaliBLE"]),
    ],
    targets: [
        .target(
            name: "CCodec",
            path: "csrc",
            sources: ["codec.c", "ports.c"],
            publicHeadersPath: "swiftpm"   // module.modulemap there: Swift sees only the linked API
        ),
        .target(
            name: "CControl",
            dependencies: ["CCodec"],
            path: "firmware/components/cali_core",
            sources: ["control.c"],
            publicHeadersPath: "swiftpm",
            cSettings: [.headerSearchPath("include")]   // control.c's own includes; Swift never sees this dir
        ),
        .target(
            name: "CaliCore",
            dependencies: ["CCodec", "CControl"],
            path: "ios/CaliKit/Sources/CaliCore",
            resources: [.copy("Resources/semantics_tables.js"), .copy("Resources/semantics.js")]
        ),
        .target(
            name: "CaliBLE",
            dependencies: ["CaliCore"],
            path: "ios/CaliKit/Sources/CaliBLE"
        ),
        .testTarget(
            name: "CaliBLETests",
            dependencies: ["CaliBLE", "CaliCore"],
            path: "ios/CaliKit/Tests/CaliBLETests"
        ),
        .testTarget(
            name: "CaliCoreTests",
            dependencies: ["CaliCore"],
            path: "ios/CaliKit/Tests/CaliCoreTests"
        ),
    ]
)
