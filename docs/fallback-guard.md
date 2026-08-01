# Automatic fallback guard (macOS and Linux/WSL)

AI smartbar can install a machine-wide Claude Code policy that blocks the two
saved **automatic** Fable-to-other-model routes:

- a safety-classifier handoff (`switchModelsOnFlag: false`); and
- the availability/overload chain (`fallbackModel: []`).

It deliberately does not select a model and does not restrict the model picker.
Manual Opus remains available through `/model opus`, `--model opus`, or any
other explicit model choice. An explicit per-run `--fallback-model` is also a
deliberate override, not an automatic route saved on the machine.

These are separate Claude Code controls documented by Anthropic under
[ask before switching](https://code.claude.com/docs/en/model-config#ask-before-switching),
[fallback model chains](https://code.claude.com/docs/en/model-config#fallback-model-chains),
and [managed-settings precedence](https://code.claude.com/docs/en/settings#settings-precedence).

## Protect this device

Open the AI smartbar popover, expand **Auto fallback**, and click **Protect**.
The compact status row is present even before usage/accounts load. Protect asks
for administrator authorization once and atomically installs one root-owned
fragment:

| Platform | Authorization | Installed fragment |
|---|---|---|
| macOS | Native administrator prompt | `/Library/Application Support/ClaudeCode/managed-settings.d/99-ai-smartbar-auto-fallback-guard.json` |
| Linux/WSL | PolicyKit through `pkexec` | `/etc/claude-code/managed-settings.d/99-ai-smartbar-auto-fallback-guard.json` |

The fragment contains only:

```json
{
  "switchModelsOnFlag": false,
  "fallbackModel": []
}
```

The policy is independent of a project directory, terminal, IDE, account
switch, and the AI smartbar process. It remains active after the app quits or
is uninstalled. Existing Claude Code processes normally live-reload managed
settings, but a request already in flight cannot be recalled; finish or cancel
that turn before relying on the new state.

On Linux, Protect and Remove require `/usr/bin/pkexec` plus a working graphical
PolicyKit authentication agent. Minimal, headless, SSH-only, and many WSL
environments do not provide that prompt; the action then returns a diagnostic
instead of silently falling back to an unsafe write. Read-only **status** still
works, and **Verify** remains an explicit action, but mutations should be run
from an interactive desktop with PolicyKit or deployed by the machine's normal
administrator-managed configuration workflow. This limitation applies to
policy mutation; Verify itself never invokes `pkexec`.

From the AI_smartbar checkout, the equivalent terminal commands are (omit
`./bin/` if `ai-smartbar` is already on your `PATH`):

```bash
./bin/ai-smartbar --fallback-guard status
./bin/ai-smartbar --fallback-guard enable
./bin/ai-smartbar --fallback-guard verify
```

Every command prints a JSON result suitable for diagnostics or automation.
`status` is read-only. `enable` is idempotent and does not rewrite
`~/.claude/settings.json`.

## What the status means

- **Protected**: the effective local managed policy is correct and the latest
  live check passed on the installed Claude Code version.
- **Protected + inconclusive**: the static policy is correct, but no current
  live check exists or the classifier-positive sentinel did not flag. This is
  intentionally not reported as a failure: classifier behavior can change.
- **Action needed**: a malformed or insecure policy file, a later conflicting
  managed fragment, a higher managed source, or live routing evidence prevents
  the app from proving protection.
- **Not protected**: neither the app-owned fragment nor another readable
  effective managed policy blocks both routes.

The inspection rejects symlinks, wrong ownership or mode, malformed JSON, and
same-source overrides. A server-, MDM-, or helper-managed policy may supersede
local files; unknown or unreadable higher policy fails closed instead of
showing a reassuring green badge.

## Live verification

**Verify** runs three fresh, tool-free Claude Code probes in an empty temporary
directory:

1. a neutral Fable control;
2. a known classifier-positive Fable routing sentinel; and
3. an explicit manual Opus control.

The first two fail immediately if any Opus service is observed. The second must
emit the structured `model_refusal_no_fallback` event; if the classifier no
longer flags that harmless diagnostic, the result is **inconclusive**, not a
false pass or false failure. The third proves that the guard itself has not
removed deliberate Opus selection.

The verifier uses a configured aggregate budget limit of US$0.25 and displays
the actual cost Claude Code reports. Claude Code's `--max-budget-usd` is a
request gate, not an absolute billing ceiling: a single API request may finish
above its assigned amount. Only timestamps, version, model attribution,
request IDs, outcomes, and costs are saved; prompts and response text are not.

## Scope and removal

The file policy covers Claude Code sessions running locally in the environment
where it was installed. A macOS policy does not govern a Linux host; a WSL
policy governs that WSL environment, not its Windows host. None can govern a
cloud session, the other end of SSH, another computer, or a future Claude Code
implementation that stops honoring these documented settings. Re-run
**Verify** after a Claude Code update or policy change.

Removal is intentionally not an ordinary toggle. Expand the advanced control,
confirm **Remove protection**, and authorize the platform again. The native
Linux tray menu exposes the same status and two-step advanced removal when the
painted popover is unavailable. The app removes only its exact, unmodified,
regular root-owned fragment; it will not delete an administrator-edited or
substituted file. The terminal equivalent is:

```bash
./bin/ai-smartbar --fallback-guard remove
```

Removing the app does not remove the policy. Remove the protection explicitly
first if that is the desired outcome.
