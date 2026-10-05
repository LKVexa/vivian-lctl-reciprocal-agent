# Third-party and upstream notices

## BottleRocket independent verifier

`brim_ref.py` is an unchanged copy of `independent/brim_ref.py` from the supplied
BottleRocket 5.0.1 VM (110K) release-candidate scaffold. It retains the supplied
Apache License, Version 2.0 terms and upstream notices.

- Full supplied license: [BOTTLEROCKET_LICENSE](BOTTLEROCKET_LICENSE)
- Original notice: [BOTTLEROCKET_NOTICE](BOTTLEROCKET_NOTICE)
- Copyright 2026 Russell Philip Smithson

Both files are preserved byte-for-byte, including the upstream license's
statement concerning the "Medium" VM. The root Product Preview Tester License
does not replace, narrow or relicense the upstream rights in these files.

The parser optionally imports `cryptography` for upstream signature-verification
functions. That package is not bundled and is not needed for the unsigned policy
profile used here. If installed independently, its own terms apply.

## Original project material

The LCTL policy, bounded `runtime.py` adapter, new `agent.py` runner, tests,
examples, manifest and documentation are project material covered by the root
LICENSE, except for the upstream material expressly identified above.

No executable implementations of catalogue-listed agent frameworks, reference
PDFs, trained weights, vendor archive, or native BottleRocket compiler are included.
