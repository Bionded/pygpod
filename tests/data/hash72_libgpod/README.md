Reference vector for hash72 (iPod nano 5G scheme), written by **libgpod 0.8.3** (Debian trixie arm64) with
`integration_tests/libgpod_tool.c build` on 2026-10-18: one track, nano 5G SysInfo (`xC027`,
FirewireGuid `0x000A270012345678`), and a HashInfo with random part `01..0c` and IV `0x64..0x73`.
libgpod used that HashInfo to sign `iTunesDB`; pygpod must produce the identical signature from it.
