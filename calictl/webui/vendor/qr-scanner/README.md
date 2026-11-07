# qr-scanner (vendored)

The remote app (`window.OC_REMOTE`, R_UI_REMOTE_APP) reads the van's setup and repair QR codes with
[qr-scanner](https://github.com/nimiq/qr-scanner) **1.4.2** by Nimiq, MIT licence (`LICENSE` in
this directory). Only the remote app loads it: the ESP's web bundle does not contain it
(`tests/test_app_bundle.py` checks this), so `WEB_APP_HTML_GZ_LEN` does not grow.

The two files are unchanged copies from the npm registry tarball
`https://registry.npmjs.org/qr-scanner/-/qr-scanner-1.4.2.tgz`, whose integrity is
`sha512-kV1yQUe2FENvn59tMZW6mOVfpq9mGxGf8l6+EGaXUOd4RBOLg7tRC83OrirM5AtDvZRpdjdlXURsHreAOSPOUw==`
(`npm view qr-scanner@1.4.2 dist.integrity`).

| File | sha256 |
|---|---|
| `qr-scanner.umd.min.js` | `40aa3fe4e1083073d7898c0895e98d5aa4d469a5c1aab6f357c0284ef75b413d` |
| `qr-scanner-worker.min.js` | `f4d8445f5a15c4e5f71a8c8c062c6443f09db370b8d3fe1fbdc7fe7889630d14` |
| `LICENSE` | `f9e6b44d80d0eb44442c7e66af8a756709a61e4d9adf0a54b8951a92ece734e2` |

`qr-scanner.umd.min.js` is a classic script that defines `QrScanner`. It loads the worker with
`import("./qr-scanner-worker.min.js")`, relative to its own URL, so the two files must stay in the
same directory. The worker runs from a `blob:` URL: a Content-Security-Policy of the remote app
must allow `worker-src blob:`.

To update: run `npm pack qr-scanner@<version>` in an empty directory, compare the tarball's
sha512 with `npm view qr-scanner@<version> dist.integrity`, copy the two files and `LICENSE`, and
update the version and the hashes here.
