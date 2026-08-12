// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "AISmartbar",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "AISmartbar", targets: ["AISmartbar"])
    ],
    targets: [
        .executableTarget(
            name: "AISmartbar",
            path: "Sources/AISmartbar"
        )
    ]
)
