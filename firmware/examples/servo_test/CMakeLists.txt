# Standalone ESP-IDF project that exercises the servo component on the
# v1 ESP-WROOM-32 hardware tonight (SG90 plastic + DOIT V1, no v2/v3
# parts yet). Steps through the four named postures, demos the
# servo_look_toward snap-and-settle, and lets idle drift run for a
# while so you can see the alive-feel pans.
#
# Build / flash / monitor:
#   cd firmware/examples/servo_test
#   idf.py set-target esp32
#   idf.py build
#   idf.py -p COM3 flash
#   idf.py -p COM3 monitor

cmake_minimum_required(VERSION 3.16)

# Pull in the reusable servo component from ../../components/.
set(EXTRA_COMPONENT_DIRS "../../components")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(servo_test)
