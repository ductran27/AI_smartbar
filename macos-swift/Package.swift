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
    dependencies: [
        // The auto-updater for DMG-installed copies. A checkout keeps
        // updating itself with git (smartbar/update_runner.py); a bundle a
        // user dragged out of a DMG has no checkout to pull, so it updates
        // through Sparkle's signed appcast instead. Which path a given copy
        // takes is decided at runtime from the bundle's SMARTBARDistribution
        // key — see Distribution.swift — so the framework is linked into
        // every build but only ever *started* in a DMG one.
        .package(url: "https://github.com/sparkle-project/Sparkle",
                 .upToNextMajor(from: "2.6.0")),
    ],
    targets: [
        .executableTarget(
            name: "AISmartbar",
            dependencies: [
                .product(name: "Sparkle", package: "Sparkle"),
            ],
            path: "Sources/AISmartbar",
            linkerSettings: [
                // `swift build` links against Sparkle.framework in .build and
                // rpaths that directory so the binary runs from there. The
                // shipped copy lives in the bundle instead, so add the rpath
                // Finder launches resolve: Contents/MacOS/AISmartbar looking
                // up one level into Contents/Frameworks. install/*.sh copy the
                // framework there; without this the bundled app cannot find
                // it and dyld kills it on launch.
                .unsafeFlags(["-Xlinker", "-rpath",
                              "-Xlinker", "@executable_path/../Frameworks"]),
            ]
        )
    ]
)
