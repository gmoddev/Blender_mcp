# ADR 0011: OS-Backed Credential Store

- Status: Accepted
- Date: 2026-09-11

## Context

The control credential lived in Blender Addon Preferences and provider secrets were registered on
`bpy.types.Scene`. Scene properties can be serialized into ordinary, shared, and temporary `.blend`
files. Addon Preferences avoid that particular sink but do not provide the platform access controls
required by Foundation 0G.

Opening a `.blend` is an untrusted-input operation. Automatically copying a legacy Scene value into
the user's trusted credential store would let a file replace the user's provider credential. Blender
operator string properties are also not an acceptable replacement because operator history and redo
state can retain their values.

## Decision

`core/credential_store.py` owns fixed-purpose credential slots under the service
`gmoddev.BlenderMCP`. It uses the pinned Python `keyring` package and accepts only its core Windows,
macOS, Secret Service, libsecret, and KWallet OS backends. Null, failure, chained third-party, and
unrecognized backends fail closed. A core OS backend may be selected from a chain, but the chain is
never trusted as a whole.

The control token resolves in this order: an explicit embedding argument, the OS store, then the
process-scoped `BLENDER_MCP_AUTH_TOKEN` compatibility override when the named OS entry is absent or
the backend is unavailable. A keyring read or value error fails closed instead of restoring an older
environment token. Both Blender and the stdio bridge use the same slot. Rotation writes the OS store
before changing live server state; failed storage leaves the active credential unchanged.

Provider credential slots exist, but the provider actions remain quarantined. No provider secret is
registered or drawn as Scene data. Secure provider-entry UI is deferred until it can prove values do
not enter operator history, undo state, logs, or saved data.

Legacy Scene fields are detected by name without reading their values. They are not silently
migrated or deleted. The UI warns and offers an explicit, confirmed cleanup operation across open
scenes. Cleanup never copies or logs the values.

## Consequences

- New `.blend` files cannot acquire provider secrets through registered add-on Scene properties.
- Existing files may retain dormant legacy values until the user runs cleanup and saves the file.
- A missing dependency, locked keyring, or unapproved backend produces a redacted failure and does
  not fall back to plaintext storage.
- Packaged Blender installation must include the pinned keyring runtime and its platform dependency;
  release packaging and live platform ACL inspection remain required before AUTH-003 is complete.
- Provider status remains configuration-only and must not read credential slots.
