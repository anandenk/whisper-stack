# whisper-stack ESP-WROOM-32 firmware — top-level project file.
# Target: ESP32 (LX6 classic), set via `idf.py set-target esp32`.
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(whisper_stack_device)
