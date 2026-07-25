// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MerlinMac",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "MerlinMac", targets: ["MerlinMac"])
    ],
    targets: [
        .executableTarget(name: "MerlinMac"),
        .testTarget(name: "MerlinMacTests", dependencies: ["MerlinMac"])
    ]
)
