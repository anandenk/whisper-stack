# Diagnostic and Benchmark Tools

> [!WARNING]
> **LAN Diagnostic Utilities**: The scripts in this directory are historical diagnostic tools and benchmarks intended exclusively for private local-network development.
>
> - **Throughput Benchmark Sink (`throughput_sink.py`)**: Binds by default to `0.0.0.0:5000` without authentication to measure raw TCP/HTTP audio throughput from physical ESP32 boards across a LAN. The `/listen` diagnostic route buffers the incoming request body in memory (`request.get_data(cache=False)`).
> - **Exposure**: These tools do not implement access control, rate limiting, or encrypted transport and must never be exposed to public networks or untrusted hosts.
