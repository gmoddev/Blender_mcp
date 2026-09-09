# Capability Authorization Model

## Modes

- **Safe Mode** permits only explicitly audited `READ` actions.
- **Full Structured Mode** is Safe Mode off with raw Python off; it permits `READ` and `MUTATE`.
- **Raw Code Mode** additionally requires the separate `Allow Raw Python` preference. It is not a
  sandbox and executes with the Blender process and OS user's authority.

Supported capability names are `READ`, `MUTATE`, `FILESYSTEM_READ`, `FILESYSTEM_WRITE`, `NETWORK`,
`EXECUTE_CODE`, `PROCESS`, and `CREDENTIAL_ACCESS`. Missing, empty, or unknown classifications deny.
An action requiring multiple capabilities is allowed only when every required capability is allowed.

## Initial Migration State

Registry metadata is action-level. The four raw execution routes are explicitly
`EXECUTE_CODE`: `manage_scripting/EXECUTE_CODE`, `manage_scripting/EXECUTE_TEXT_BLOCK`,
`execute_blender_code`, and the legacy `execute_code`. Core status, discovery, and validation are
explicit `READ`.

Existing unaudited structured actions receive a conservative `MUTATE` migration classification at
registration. Therefore Safe Mode blocks them rather than inferring safety from names such as
`get`, `list`, or `inspect`. This is scaffolding, not completion: Foundation 0C must replace every
migration default with an explicit action audit before Safe Mode is considered production-ready.

## Registration Contract

New handlers must supply action-level capability metadata to `register_handler`. Authorization runs
after tool/action lookup and before schema processing or handler invocation. A UI label never grants
authority by itself; tests must exercise the dispatcher boundary.

External integrations and filesystem/process actions remain denied when explicitly classified with
their dedicated capabilities until Foundation 0E-0G provide user-controlled policy.
