# Historical Firmware (May 2026 Prototype)

> [!WARNING]
> **Unsupported Historical Code**: This directory contains the ESP-IDF firmware for ESP32 and ESP32-S3 boards from the May 2026 prototype. It is preserved for reference and hardware milestone continuity. It is not maintained for production use and exhibits known security limitations:
>
> - **Plaintext Transport**: Network communication with the server operates over unencrypted HTTP (`firmware/main/http_client.c`). Both digitized audio streams and optional `X-API-Key` authentication headers travel in cleartext across the local network.
> - **Omitted 802.1X Server Validation**: In WPA2-Enterprise mode (`firmware/main/wifi.c`), server CA certificate verification is omitted by design for simplicity on campus/enterprise networks. On untrusted or rogue access points, credentials sent via PEAP-MSCHAPv2 could be captured.
> - **Environment**: Designed solely for personal bench testing on isolated, trusted local networks.

For current architecture and next device milestones, consult [`docs/PROJECT.md`](../docs/PROJECT.md) and [`README.md`](../README.md).
