// A system-wide hotkey through Carbon's RegisterEventHotKey — the API every
// hotkey library is built on. Unlike NSEvent.addGlobalMonitorForEvents
// (.keyDown) it needs no Accessibility / Input Monitoring permission, and the
// system wakes this app only for the one registered combination instead of
// for every keystroke on the Mac. Windows' RegisterHotKey is the same idea.
import Carbon.HIToolbox

final class GlobalHotkey {
    private var hotKeyRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    private let action: () -> Void

    /// nil when the system refuses the registration (another app owns the
    /// combination). `modifiers` are Carbon's: controlKey, optionKey, …
    init?(keyCode: Int, modifiers: Int, action: @escaping () -> Void) {
        self.action = action
        var pressed = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                    eventKind: UInt32(kEventHotKeyPressed))
        // The C callback cannot capture: it finds this object again through
        // the opaque pointer registered with it. Unretained is safe because
        // deinit removes the handler before the object goes away.
        let installed = InstallEventHandler(
            GetApplicationEventTarget(),
            { _, _, context in
                guard let context else { return OSStatus(eventNotHandledErr) }
                Unmanaged<GlobalHotkey>.fromOpaque(context)
                    .takeUnretainedValue().action()
                return noErr
            },
            1, &pressed, Unmanaged.passUnretained(self).toOpaque(),
            &handlerRef)
        let identity = EventHotKeyID(signature: OSType(0x4149_5342),  // 'AISB'
                                     id: 1)
        guard installed == noErr,
              RegisterEventHotKey(UInt32(keyCode), UInt32(modifiers), identity,
                                  GetApplicationEventTarget(), 0,
                                  &hotKeyRef) == noErr
        else {
            unregister()
            return nil
        }
    }

    deinit { unregister() }

    private func unregister() {
        if let hotKeyRef { UnregisterEventHotKey(hotKeyRef) }
        if let handlerRef { RemoveEventHandler(handlerRef) }
        hotKeyRef = nil
        handlerRef = nil
    }
}
