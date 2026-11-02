# Camera runtime sources

The optional runtime is built with `powershell -File tools/build_camera_bridge.ps1`.
It combines MIT-licensed [go2rtc v1.9.14](https://github.com/AlexxIT/go2rtc/tree/v1.9.14),
commit `b5948cfb25404cc5cb37b166ecaa2dca20b11d4b`, with the public
[homebridge-xiaomi-go2rtc control patch](https://github.com/moguennouni/homebridge-xiaomi-go2rtc/tree/main/go2rtc-xiaomi-control).
The patch is vendored here so builds do not silently follow upstream changes.

Patch SHA-256: `ccf99e77ca9e9a8c90c864fbfb2c41db0cff76eddb51090689ce53e29bfaaee3`.
The upstream go2rtc MIT license is retained in `LICENSE.go2rtc` and copied beside the binary.
The camera-control patch is an unofficial MIT-licensed extension, not endorsed by the go2rtc author.
Its author's copyright and license are retained in `LICENSE.control`, also copied beside the binary.

The app starts its own hidden runtime per preview, listening only on loopback with
WebRTC disabled. LAN mode passes the existing account's pass token in a temporary configuration;
remote mode supplies only the camera's ephemeral MISS authorization and key exchange.
It deletes that file after startup and terminates the runtime when preview stops.
Media and diagnostic logs are not written to disk. Both modes request video without audio;
the app does not attach an audio output or record media. Remote provisioning goes through
the existing service adapter; authorization stays in the helper, never in the Qt player or app logs.
Only explicit presses on direction buttons send motor commands; holding repeats steps
with at least 800ms after each acknowledgement. Release, pointer exit, window deactivation,
stop, close or an error cancels further repeats, including queued repeat jobs.
No automatic tracking is added.

`relay.go` is this project's MIT-licensed CS2 relay transport, with wire-format and
destination-guard regressions in `relay_test.go`. The protocol investigation used
[micam](https://github.com/eugeneRover/micam) as a reference and verified the exchange
against a real camera; its Java source and binaries are not bundled. The Go transport
uses only the authenticated camera configuration's discovery servers and endpoints
introduced by those servers, validates reply sources, and blocks the two declined
relay addresses. No static fallback pool is used. `go2rtc-xiaomi-relay.patch` selects
this transport only when `relay_init` is supplied; LAN behavior is preserved.
The build script verifies all relay source hashes and runs protocol regressions.

`go2rtc-xiaomi-ptz.patch` normalizes plaintext/encrypted response command words
and requires MOTOR_RESP (0x113) for MOTOR_REQ (0x112), ignoring unrelated events.
The application accepts null-terminated JSON replies and requires an integer ret=0;
nonzero device states remain visible and are not misreported as malformed JSON.
`ptz_test.go` verifies response matching with interleaved unrelated responses.
