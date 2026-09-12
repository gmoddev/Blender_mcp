# Bundled Wheel Inventory

The Windows x64 Blender extension bundles the following unmodified Python wheels from PyPI. The
release builder verifies each SHA-256 digest against `blender_mcp/wheels/SHA256SUMS` before invoking
Blender's extension builder. Every wheel retains its original distribution metadata and license
files.

| Distribution | Version | License | SHA-256 |
|---|---:|---|---|
| `jaraco.classes` | 3.4.0 | MIT | `f662826b6bed8cace05e7ff873ce0f9283b5c924470fe664fff1c2f00f581790` |
| `jaraco.context` | 6.1.2 | MIT | `bf8150b79a2d5d91ae48629d8b427a8f7ba0e1097dd6202a9059f29a36379535` |
| `jaraco.functools` | 4.6.0 | MIT | `99e3dc0060c5cbe8fcd1cdb36258e2a65ca40f1566b2033b12abb1bb44dd3c30` |
| `keyring` | 25.7.0 | MIT | `be4a0b195f149690c166e850609a477c532ddbfbaed96a404d4e43f8d5e2689f` |
| `more-itertools` | 11.1.0 | MIT | `4b65538ae22f6fed0ce4874efd317463a7489796a0939fa66824dd542125a192` |
| `pywin32-ctypes` | 0.2.3 | BSD-3-Clause | `8a1513379d709975552d202d942d9837758905c8d01eb82b8bcc30918929e7b8` |

Source project pages:

- <https://pypi.org/project/jaraco.classes/3.4.0/>
- <https://pypi.org/project/jaraco.context/6.1.2/>
- <https://pypi.org/project/jaraco.functools/4.6.0/>
- <https://pypi.org/project/keyring/25.7.0/>
- <https://pypi.org/project/more-itertools/11.1.0/>
- <https://pypi.org/project/pywin32-ctypes/0.2.3/>

The current artifact deliberately declares only `windows-x64`. Adding macOS or Linux requires the
complete platform dependency set, a separately built artifact, backend allowlist validation, and a
live disposable credential round trip on that OS.
