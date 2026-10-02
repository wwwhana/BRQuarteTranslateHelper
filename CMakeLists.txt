cmake_minimum_required(VERSION 3.24)

project(BRQuarteTranslateHelper VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(CTest)
set(BRQ_GAME_ROOT "" CACHE PATH "Game installation directory")
set(BRQ_SOURCE_GAME_ROOT "" CACHE PATH "Game directory to read for extraction; defaults to BRQ_GAME_ROOT")
if(NOT BRQ_SOURCE_GAME_ROOT)
    set(BRQ_SOURCE_GAME_ROOT "${BRQ_GAME_ROOT}")
endif()
option(BRQ_BUILD_TRANSLATION "Build translations directly from the selected game installation" OFF)
option(BRQ_EMBED_ASSETS "Put generated translation assets in the ASI as RCDATA" OFF)
option(BRQ_ENABLE_GAME_CHECKS "Enable checks that need local game analysis and translation files" OFF)
include(cmake/AssetInputs.cmake)

if(BUILD_TESTING)
    foreach(check IN ITEMS path_policy glyph_table font_descriptor b0_index_hash source_pack runtime_text)
        add_executable(${check}_test tests/${check}_test.cpp)
        target_include_directories(${check}_test PRIVATE src)
        if(MSVC)
            target_compile_options(${check}_test PRIVATE /W4 /WX /permissive- /utf-8)
        else()
            target_compile_options(${check}_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
        endif()
        if(MINGW)
            target_link_options(${check}_test PRIVATE -static)
        endif()
        add_test(NAME ${check} COMMAND ${check}_test)
    endforeach()
    target_sources(path_policy_test PRIVATE src/path_policy.cpp)
    find_package(Python3 3.11 QUIET COMPONENTS Interpreter)
    if(Python3_FOUND)
        set(BRQ_TABLE_CHECK_ARGS)
        if(BRQ_TARGET_LANGUAGE)
            list(APPEND BRQ_TABLE_CHECK_ARGS --translations-root "${BRQ_TRANSLATIONS_ROOT}"
                --target-language "${BRQ_TARGET_LANGUAGE}")
        endif()
        add_test(NAME translation_tables COMMAND "${Python3_EXECUTABLE}" -B
            "${CMAKE_CURRENT_SOURCE_DIR}/tools/translation_table.py" --self-test
            ${BRQ_TABLE_CHECK_ARGS})
        foreach(check IN ITEMS graphic_assets prepare_runtime font_input asset_build pak_source export_text build_translation kscl bsb)
            add_test(NAME ${check} COMMAND "${Python3_EXECUTABLE}" -B
                "${CMAKE_CURRENT_SOURCE_DIR}/tests/${check}_test.py")
        endforeach()
        execute_process(COMMAND "${Python3_EXECUTABLE}" -c "import PIL, ispc_texcomp"
            RESULT_VARIABLE BRQ_GRAPHICS_PYTHON_MISSING OUTPUT_QUIET ERROR_QUIET)
        if(BRQ_GRAPHICS_PYTHON_MISSING EQUAL 0)
            foreach(check IN ITEMS dds_overlay graphics_templates export_localization)
                add_test(NAME ${check} COMMAND "${Python3_EXECUTABLE}" -B
                    "${CMAKE_CURRENT_SOURCE_DIR}/tests/${check}_test.py")
            endforeach()
        endif()
    endif()
endif()

if(BRQ_ENABLE_GAME_CHECKS AND NOT BRQ_BUILD_TRANSLATION)
    if(NOT BUILD_TESTING)
        message(FATAL_ERROR "BRQ_ENABLE_GAME_CHECKS requires BUILD_TESTING=ON")
    endif()
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    if(NOT EXISTS "${BRQ_GAME_ROOT}/tools/source_dictionary.py")
        message(FATAL_ERROR "Game checks require the local translation tools under BRQ_GAME_ROOT/tools")
    endif()
    add_test(NAME source_dictionary COMMAND "${Python3_EXECUTABLE}" -B
        "${BRQ_GAME_ROOT}/tools/source_dictionary.py" --self-test)
    set(BRQ_INVENTORY_ROOT "" CACHE PATH "Game index inventory for optional analysis checks")
    if(NOT IS_DIRECTORY "${BRQ_INVENTORY_ROOT}")
        message(FATAL_ERROR "Game checks require BRQ_INVENTORY_ROOT")
    endif()
    add_test(NAME b0_index_inventory COMMAND b0_index_hash_test "${BRQ_INVENTORY_ROOT}")
endif()

if(NOT WIN32)
    return()
endif()

if(NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "BRQuartetKorean requires a 64-bit Windows toolchain")
endif()

# Manual Windows check, run with the original B0..B4 DLLs as the working directory.
add_executable(module_address_test EXCLUDE_FROM_ALL tests/module_address_test.cpp)
target_include_directories(module_address_test PRIVATE src)

enable_language(RC)

option(BRQ_EMBED_ASSETS "Put allowlisted runtime assets in the ASI as RCDATA" OFF)
option(BRQ_DISABLE_B0_HASH "Keep the original B0 hash code for an audio A/B check" OFF)
option(BRQ_ENABLE_DIAGNOSTICS "Enable verbose file/font traces and diagnostic-only hooks" OFF)

if(BRQ_EMBED_ASSETS)
    set(BRQ_DIST_MODE embedded)
else()
    set(BRQ_DIST_MODE external)
endif()
set(BRQ_DIST_DIR "${CMAKE_CURRENT_SOURCE_DIR}/dist/${BRQ_DIST_MODE}")

set(BRQ_SIGNATURES_JSON "" CACHE FILEPATH "Verified loader analysis")
set(BRQ_GLYPH_SIGNATURES_JSON ""
    CACHE FILEPATH "Verified glyph lookup analysis")
set(BRQ_ORIGINAL_HASHES_JSON ""
    CACHE FILEPATH "Verified original game file hashes")
set(BRQ_INSTALLED_HASHES_JSON "" CACHE FILEPATH "Optional explicit before-build snapshot for a modded installation")
set(BRQ_INSTALLED_HASH_ARGS)
if(BRQ_INSTALLED_HASHES_JSON)
    list(APPEND BRQ_INSTALLED_HASH_ARGS --installed-hashes "${BRQ_INSTALLED_HASHES_JSON}")
endif()
set(BRQ_MINHOOK_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/external/minhook" CACHE PATH "MinHook v1.3.4 submodule")

if(NOT EXISTS "${BRQ_MINHOOK_ROOT}/include/MinHook.h" OR NOT EXISTS "${BRQ_MINHOOK_ROOT}/LICENSE.txt")
    message(FATAL_ERROR "MinHook is missing. Run: git submodule update --init external/minhook")
endif()
if(BRQ_EMBED_ASSETS)
    if(NOT BRQ_EFFECTIVE_RUNTIME_ROOT)
        message(FATAL_ERROR "Embedded builds require generated translation assets")
    endif()
endif()

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
add_subdirectory("${BRQ_MINHOOK_ROOT}" "${CMAKE_BINARY_DIR}/minhook" EXCLUDE_FROM_ALL)

set(BRQ_GENERATED_DIR "${CMAKE_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${BRQ_GENERATED_DIR}")
find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
file(GLOB BRQ_RUNTIME_TEXT_INPUTS CONFIGURE_DEPENDS "${BRQ_TRANSLATIONS_ROOT}/*.json")
add_custom_command(OUTPUT "${BRQ_GENERATED_DIR}/runtime_text_data.hpp"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/runtime_text.py"
        --workspace "${BRQ_TRANSLATIONS_ROOT}" --language "${BRQ_TARGET_LANGUAGE}"
        --output "${BRQ_GENERATED_DIR}/runtime_text_data.hpp"
    DEPENDS tools/runtime_text.py tools/translation_table.py data/runtime_text_hooks.json ${BRQ_RUNTIME_TEXT_INPUTS}
    VERBATIM)
add_custom_target(brq-runtime-text DEPENDS "${BRQ_GENERATED_DIR}/runtime_text_data.hpp")
if(BRQ_EFFECTIVE_RUNTIME_ROOT)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    add_custom_command(OUTPUT "${BRQ_GENERATED_DIR}/source_pack_data.hpp"
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/build_translation.py"
                --check-runtime "${BRQ_EFFECTIVE_RUNTIME_ROOT}" --header "${BRQ_GENERATED_DIR}/source_pack_data.hpp"
        DEPENDS ${BRQ_ASSET_DEPENDS} tools/build_translation.py VERBATIM)
    add_custom_target(brq-asset-metadata DEPENDS "${BRQ_GENERATED_DIR}/source_pack_data.hpp")
    if(TARGET brq-assets)
        add_dependencies(brq-asset-metadata brq-assets)
    endif()
else()
    # Reviewed path/hash metadata only; no game text, fonts or other game assets.
    configure_file(data/source_pack_data.hpp "${BRQ_GENERATED_DIR}/source_pack_data.hpp" COPYONLY)
endif()

add_executable(module_trace_test EXCLUDE_FROM_ALL tests/module_trace_test.cpp src/path_policy.cpp)
target_include_directories(module_trace_test PRIVATE src "${BRQ_GENERATED_DIR}")
target_link_libraries(module_trace_test PRIVATE minhook bcrypt)
target_compile_definitions(module_trace_test PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_definitions(module_trace_test PRIVATE BRQ_ENABLE_DIAGNOSTICS=1)
if(NOT MSVC)
    target_compile_options(module_trace_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
if(MINGW)
    target_link_options(module_trace_test PRIVATE -static)
endif()

add_executable(module_lifecycle_test EXCLUDE_FROM_ALL tests/module_lifecycle_test.cpp src/path_policy.cpp)
target_include_directories(module_lifecycle_test PRIVATE src "${BRQ_GENERATED_DIR}")
target_link_libraries(module_lifecycle_test PRIVATE minhook bcrypt)
target_compile_definitions(module_lifecycle_test PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_definitions(module_lifecycle_test PRIVATE BRQ_ENABLE_DIAGNOSTICS=1)
target_compile_definitions(module_lifecycle_test PRIVATE BRQ_DISABLE_B0_HASH=$<BOOL:${BRQ_DISABLE_B0_HASH}>)
if(NOT MSVC)
    target_compile_options(module_lifecycle_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()
if(MINGW)
    target_link_options(module_lifecycle_test PRIVATE -static)
endif()

set(BRQ_GENERATED_SOURCES)
set(BRQ_EMBED_FLAG 0)
if(BRQ_EMBED_ASSETS)
    set(BRQ_EMBED_FLAG 1)
    add_custom_command(OUTPUT "${BRQ_GENERATED_DIR}/embedded_assets.hpp" "${BRQ_GENERATED_DIR}/embedded_assets.rc"
        COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/generate_embedded.py"
                --runtime-root "${BRQ_EFFECTIVE_RUNTIME_ROOT}"
                --translations-root "${BRQ_TRANSLATIONS_ROOT}"
                --header "${BRQ_GENERATED_DIR}/embedded_assets.hpp"
                --resource "${BRQ_GENERATED_DIR}/embedded_assets.rc"
        DEPENDS ${BRQ_ASSET_DEPENDS} tools/generate_embedded.py VERBATIM)
    add_custom_target(brq-embedded-assets DEPENDS
        "${BRQ_GENERATED_DIR}/embedded_assets.hpp" "${BRQ_GENERATED_DIR}/embedded_assets.rc")
    if(TARGET brq-assets)
        add_dependencies(brq-embedded-assets brq-assets)
    endif()
    list(APPEND BRQ_GENERATED_SOURCES "${BRQ_GENERATED_DIR}/embedded_assets.rc")
endif()

add_library(BRQuartetKorean SHARED
    src/loader.cpp
    src/path_policy.cpp
    ${BRQ_GENERATED_SOURCES}
)
foreach(target IN ITEMS BRQuartetKorean module_trace_test module_lifecycle_test)
    if(TARGET ${target})
        if(TARGET brq-asset-metadata)
            add_dependencies(${target} brq-asset-metadata)
        endif()
        if(TARGET brq-embedded-assets)
            add_dependencies(${target} brq-embedded-assets)
        endif()
    endif()
endforeach()
target_include_directories(BRQuartetKorean PRIVATE src "${BRQ_GENERATED_DIR}")
target_link_libraries(BRQuartetKorean PRIVATE minhook bcrypt)
target_compile_definitions(BRQuartetKorean PRIVATE
    BRQ_EMBED_ASSETS=${BRQ_EMBED_FLAG}
    BRQ_DISABLE_B0_HASH=$<BOOL:${BRQ_DISABLE_B0_HASH}>
    BRQ_ENABLE_DIAGNOSTICS=$<BOOL:${BRQ_ENABLE_DIAGNOSTICS}>
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)

if(MSVC)
    target_compile_options(BRQuartetKorean PRIVATE /W4 /WX /permissive- /utf-8)
else()
    target_compile_options(BRQuartetKorean PRIVATE
        -Wall -Wextra -Wpedantic -Werror
        "-ffile-prefix-map=${CMAKE_CURRENT_SOURCE_DIR}=BRQuartetKorean"
        "-ffile-prefix-map=${BRQ_GAME_ROOT}=."
    )
endif()
if(MINGW)
    target_link_options(BRQuartetKorean PRIVATE
        -static
        -Wl,--gc-sections
        -Wl,--dynamicbase
        -Wl,--nxcompat
        -Wl,--high-entropy-va
        -Wl,--exclude-all-symbols
    )
endif()

set_target_properties(BRQuartetKorean PROPERTIES
    OUTPUT_NAME BRQuarteTranslateHelper
    PREFIX ""
    SUFFIX ".asi"
    RUNTIME_OUTPUT_DIRECTORY "${BRQ_DIST_DIR}/$<0:>"
    LIBRARY_OUTPUT_DIRECTORY "${BRQ_DIST_DIR}/$<0:>"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)

add_custom_command(TARGET BRQuartetKorean POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${BRQ_MINHOOK_ROOT}/LICENSE.txt" "${BRQ_DIST_DIR}/LICENSE-MinHook.txt"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${CMAKE_CURRENT_SOURCE_DIR}/THIRD_PARTY_NOTICES.md" "${BRQ_DIST_DIR}/THIRD_PARTY_NOTICES.md"
    VERBATIM
)
if(LLVM_MINGW_ROOT AND EXISTS "${LLVM_MINGW_ROOT}/LICENSE.TXT")
    add_custom_command(TARGET BRQuartetKorean POST_BUILD
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                "${LLVM_MINGW_ROOT}/LICENSE.TXT" "${BRQ_DIST_DIR}/LICENSE-LLVM-MinGW.txt"
        VERBATIM
    )
endif()

if(BRQ_BUILD_TRANSLATION)
    add_custom_target(static-check
        COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/tools/build_translation.py"
            --check-runtime "${BRQ_EFFECTIVE_RUNTIME_ROOT}" --binary "$<TARGET_FILE:BRQuartetKorean>"
            --embedded "${BRQ_EMBED_FLAG}"
        DEPENDS BRQuartetKorean VERBATIM)
endif()

if(BRQ_ENABLE_GAME_CHECKS AND NOT BRQ_BUILD_TRANSLATION)
    set(BRQ_COMPILER_NOTICE_ARGS)
    set(BRQ_PUBLIC_FONT_CHECK_ARGS)
    if(BRQ_RESOLVED_FONT_TTF)
        list(APPEND BRQ_PUBLIC_FONT_CHECK_ARGS
            --font-output "${CMAKE_BINARY_DIR}/fonts" --font-ttf "${BRQ_RESOLVED_FONT_TTF}"
            --msdf-atlas-gen "${BRQ_MSDF_ATLAS_GEN_EXECUTABLE}" --gust-g1t "${BRQ_GUST_G1T_EXECUTABLE}")
    endif()
    if(LLVM_MINGW_ROOT)
        list(APPEND BRQ_COMPILER_NOTICE_ARGS --llvm-mingw-root "${LLVM_MINGW_ROOT}")
    endif()
    if(NOT BRQ_RUNTIME_ROOT OR NOT EXISTS "${BRQ_SIGNATURES_JSON}" OR
       NOT EXISTS "${BRQ_GLYPH_SIGNATURES_JSON}" OR NOT EXISTS "${BRQ_ORIGINAL_HASHES_JSON}")
        message(FATAL_ERROR "Game checks require BRQ_RUNTIME_ROOT and the three reviewed analysis JSON files")
    endif()
    add_custom_target(static-check
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/tools/static_check.py"
            --binary "$<TARGET_FILE:BRQuartetKorean>"
            --project "${CMAKE_CURRENT_SOURCE_DIR}"
            --game-root "${BRQ_GAME_ROOT}"
            --runtime-root "${BRQ_EFFECTIVE_RUNTIME_ROOT}"
            --signatures "${BRQ_SIGNATURES_JSON}"
            --glyph-signatures "${BRQ_GLYPH_SIGNATURES_JSON}"
            --original-hashes "${BRQ_ORIGINAL_HASHES_JSON}"
            --minhook-root "${BRQ_MINHOOK_ROOT}"
            --translations-root "${BRQ_TRANSLATIONS_ROOT}" --target-language "${BRQ_TARGET_LANGUAGE}"
            ${BRQ_PUBLIC_FONT_CHECK_ARGS}
            ${BRQ_COMPILER_NOTICE_ARGS}
            ${BRQ_INSTALLED_HASH_ARGS}
            --report "${BRQ_DIST_DIR}/build-report.json"
            --embedded "${BRQ_EMBED_FLAG}"
    DEPENDS BRQuartetKorean
    VERBATIM
    )
endif()

if(NOT BRQ_EMBED_ASSETS AND BRQ_EFFECTIVE_RUNTIME_ROOT)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    set(BRQ_EXTERNAL_ASSETS "${BRQ_DIST_DIR}/BRQuarteTranslateHelper-assets")
    add_custom_command(OUTPUT "${BRQ_EXTERNAL_ASSETS}/.brq-prepared-runtime"
        COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/tools/prepare_runtime.py"
            --runtime-root "${BRQ_EFFECTIVE_RUNTIME_ROOT}" --output "${BRQ_EXTERNAL_ASSETS}"
            --translations-root "${BRQ_TRANSLATIONS_ROOT}"
        DEPENDS ${BRQ_ASSET_DEPENDS} tools/prepare_runtime.py
        VERBATIM)
    add_custom_target(brq-external-assets ALL
        DEPENDS "${BRQ_EXTERNAL_ASSETS}/.brq-prepared-runtime")
    add_dependencies(BRQuartetKorean brq-external-assets)
    install(DIRECTORY "${BRQ_EFFECTIVE_RUNTIME_ROOT}/" DESTINATION BRQuarteTranslateHelper-assets
        PATTERN ".brq-*" EXCLUDE)
endif()

install(TARGETS BRQuartetKorean RUNTIME DESTINATION . LIBRARY DESTINATION .)
install(FILES "${BRQ_MINHOOK_ROOT}/LICENSE.txt" DESTINATION . RENAME LICENSE-MinHook.txt)
install(FILES THIRD_PARTY_NOTICES.md DESTINATION .)
if(LLVM_MINGW_ROOT AND EXISTS "${LLVM_MINGW_ROOT}/LICENSE.TXT")
    install(FILES "${LLVM_MINGW_ROOT}/LICENSE.TXT" DESTINATION . RENAME LICENSE-LLVM-MinGW.txt)
endif()

add_dependencies(BRQuartetKorean brq-runtime-text)
add_dependencies(module_trace_test brq-runtime-text)
add_dependencies(module_lifecycle_test brq-runtime-text)
