cmake_minimum_required(VERSION 3.16)
project(roadsign_detector LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Enable ASM for NEON preprocessing on aarch64
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    enable_language(ASM)
endif()

# Optimisation flags for Raspberry Pi 5 (Cortex-A76 / ARMv8.2-A)
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    set(CMAKE_CXX_FLAGS_RELEASE "-O3 -march=armv8.2-a+fp16+dotprod -mtune=cortex-a76 -DNDEBUG")
    set(CMAKE_ASM_FLAGS_RELEASE "-march=armv8.2-a+fp16+dotprod")
else()
    set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG")
endif()

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# ── Find OpenCV ──
find_package(OpenCV REQUIRED COMPONENTS core imgcodecs imgproc)

# ── Find ONNX Runtime ──
# Priority: -DONNXRUNTIME_DIR (cmake flag) > env var > /usr/local
if(NOT DEFINED ONNXRUNTIME_DIR)
    if(DEFINED ENV{ONNXRUNTIME_DIR})
        set(ONNXRUNTIME_DIR $ENV{ONNXRUNTIME_DIR})
    else()
        set(ONNXRUNTIME_DIR "/usr/local")
    endif()
endif()

find_library(ONNXRUNTIME_LIB onnxruntime
    HINTS ${ONNXRUNTIME_DIR}/lib ${ONNXRUNTIME_DIR}/lib64)
find_path(ONNXRUNTIME_INCLUDE onnxruntime_cxx_api.h
    HINTS ${ONNXRUNTIME_DIR}/include
          ${ONNXRUNTIME_DIR}/include/onnxruntime)

if(NOT ONNXRUNTIME_LIB OR NOT ONNXRUNTIME_INCLUDE)
    message(FATAL_ERROR "ONNX Runtime not found. Set ONNXRUNTIME_DIR.")
endif()

message(STATUS "ONNX Runtime lib: ${ONNXRUNTIME_LIB}")
message(STATUS "ONNX Runtime include: ${ONNXRUNTIME_INCLUDE}")

# ── Source files ──
set(SOURCES
    src/detector.cpp
    src/v4l2_capture.cpp
    src/main.cpp
)

# Add NEON assembly on aarch64
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
    list(APPEND SOURCES src/neon_preprocess.S)
    message(STATUS "NEON SIMD: ARM64 assembly enabled (LD3 deinterleave)")
else()
    message(STATUS "NEON SIMD: scalar fallback (non-ARM architecture)")
endif()

# ── Build main executable ──
add_executable(roadsign_server ${SOURCES})

target_include_directories(roadsign_server PRIVATE
    src/
    ${ONNXRUNTIME_INCLUDE}
    ${OpenCV_INCLUDE_DIRS}
)

target_link_libraries(roadsign_server PRIVATE
    ${ONNXRUNTIME_LIB}
    ${OpenCV_LIBS}
    pthread
)

install(TARGETS roadsign_server RUNTIME DESTINATION bin)
