// swift-tools-version: 6.0
import PackageDescription

// The C codec and the control planner are linked as they are (no copies): the same code the ESP32 satellite runs.
let package = Package(
    name: "CaliKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "CaliCore", targets: ["CaliCore"])],
    targets: [
        .target(
            name: "CCodec",
            path: "csrc",
            sources: ["codec.c"],
            publicHeadersPath: "."
        ),
        .target(
            name: "CControl",
            dependencies: ["CCodec"],
            path: "firmware/components/cali_core",
            sources: ["control.c"],
            publicHeadersPath: "include"
        ),
        .target(
            name: "CaliCore",
            dependencies: ["CCodec", "CControl"],
            path: "ios/CaliKit/Sources/CaliCore"
        ),
        .testTarget(
            name: "CaliCoreTests",
            dependencies: ["CaliCore"],
            path: "ios/CaliKit/Tests/CaliCoreTests"
        ),
    ]
)
