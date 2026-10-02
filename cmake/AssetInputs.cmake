set(BRQ_TARGET_LANGUAGE "" CACHE STRING "Target ISO 639-1 language code, for example en, fr, or zh")
string(TOLOWER "${BRQ_TARGET_LANGUAGE}" BRQ_TARGET_LANGUAGE)
set(BRQ_ISO_LANGUAGES " aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu ")
string(FIND "${BRQ_ISO_LANGUAGES}" " ${BRQ_TARGET_LANGUAGE} " BRQ_LANGUAGE_INDEX)
if(BRQ_TARGET_LANGUAGE AND (NOT BRQ_TARGET_LANGUAGE MATCHES "^[a-z][a-z]$" OR BRQ_LANGUAGE_INDEX EQUAL -1))
    message(FATAL_ERROR "BRQ_TARGET_LANGUAGE must be an ISO 639-1 code, for example en, fr, or zh")
endif()
set(BRQ_TRANSLATIONS_ROOT "" CACHE PATH "Translation workspace override; defaults to translations/<language>")
if(NOT BRQ_TRANSLATIONS_ROOT)
    set(BRQ_TRANSLATIONS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/translations/${BRQ_TARGET_LANGUAGE}")
endif()
set(BRQ_RUNTIME_ROOT "" CACHE PATH "Reviewed game-ready translation assets")
set(BRQ_GRAPHICS_ROOT "" CACHE PATH "Optional B0..B4 folders containing additional game-ready G1T graphics")
set(BRQ_MSDF_ATLAS_GEN_EXECUTABLE "" CACHE FILEPATH "Native msdf-atlas-gen executable")
set(BRQ_GUST_G1T_EXECUTABLE "" CACHE FILEPATH "Native gust_g1t executable")
include("${CMAKE_CURRENT_LIST_DIR}/FontInput.cmake")
brq_resolve_font()
include("${CMAKE_CURRENT_LIST_DIR}/LocalizationExport.cmake")

if(BRQ_BUILD_TRANSLATION OR (BRQ_EMBED_ASSETS AND NOT BRQ_RUNTIME_ROOT) OR
   (BRQ_FONT_TTF AND NOT BRQ_EXPORT_LOCALIZATION AND NOT BRQ_RUNTIME_ROOT))
    set(BRQ_BUILD_TRANSLATION ON)
    if(NOT BRQ_TARGET_LANGUAGE)
        message(FATAL_ERROR "Set BRQ_TARGET_LANGUAGE for translation builds")
    endif()
    if(NOT IS_DIRECTORY "${BRQ_SOURCE_GAME_ROOT}" OR NOT IS_DIRECTORY "${BRQ_PAK_ROOT}")
        message(FATAL_ERROR "Set BRQ_SOURCE_GAME_ROOT or BRQ_GAME_ROOT to the game directory to read")
    endif()
    if(NOT IS_DIRECTORY "${BRQ_TRANSLATIONS_ROOT}")
        message(FATAL_ERROR "Export translations/${BRQ_TARGET_LANGUAGE} before building translations")
    endif()
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    set(BRQ_TRANSLATION_OUTPUT "${CMAKE_BINARY_DIR}/translation")
    set(BRQ_EFFECTIVE_RUNTIME_ROOT "${BRQ_TRANSLATION_OUTPUT}/runtime")
    set(BRQ_TRANSLATION_ARGS --game-root "${BRQ_SOURCE_GAME_ROOT}" --pak-root "${BRQ_PAK_ROOT}"
        --translations-root "${BRQ_TRANSLATIONS_ROOT}" --language "${BRQ_TARGET_LANGUAGE}"
        --output "${BRQ_TRANSLATION_OUTPUT}" --gust-ebm "${BRQ_GUST_EBM_EXECUTABLE}")
    if(NOT EXISTS "${BRQ_GUST_EBM_EXECUTABLE}" OR IS_DIRECTORY "${BRQ_GUST_EBM_EXECUTABLE}")
        message(FATAL_ERROR "Set BRQ_GUST_EBM_EXECUTABLE for translation builds")
    endif()
    if(BRQ_GUST_ENC_EXECUTABLE)
        list(APPEND BRQ_TRANSLATION_ARGS --gust-enc "${BRQ_GUST_ENC_EXECUTABLE}")
    endif()
    if(BRQ_GUST_G1T_EXECUTABLE)
        list(APPEND BRQ_TRANSLATION_ARGS --gust-g1t "${BRQ_GUST_G1T_EXECUTABLE}")
    endif()
    if(BRQ_RESOLVED_FONT_TTF)
        foreach(tool IN ITEMS MSDF_ATLAS_GEN GUST_G1T)
            if(NOT EXISTS "${BRQ_${tool}_EXECUTABLE}" OR IS_DIRECTORY "${BRQ_${tool}_EXECUTABLE}")
                message(FATAL_ERROR "Set BRQ_${tool}_EXECUTABLE to build the workspace font")
            endif()
        endforeach()
        list(APPEND BRQ_TRANSLATION_ARGS --font "${BRQ_RESOLVED_FONT_TTF}"
            --msdf-atlas-gen "${BRQ_MSDF_ATLAS_GEN_EXECUTABLE}")
    endif()
    file(GLOB_RECURSE BRQ_WORKSPACE_INPUTS CONFIGURE_DEPENDS LIST_DIRECTORIES FALSE "${BRQ_TRANSLATIONS_ROOT}/*")
    file(GLOB BRQ_GAME_INPUTS CONFIGURE_DEPENDS "${BRQ_PAK_ROOT}/*.PAK" "${BRQ_PAK_ROOT}/*.pak" "${BRQ_SOURCE_GAME_ROOT}/B?.dll")
    file(GLOB_RECURSE BRQ_BUILD_SCRIPTS CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tools/*.py")
    file(GENERATE OUTPUT "${CMAKE_BINARY_DIR}/translation-inputs.txt" CONTENT "${BRQ_TRANSLATION_ARGS}\n")
    add_custom_command(OUTPUT "${BRQ_TRANSLATION_OUTPUT}/.brq-translation-build"
        COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/tools/build_translation.py" ${BRQ_TRANSLATION_ARGS}
        DEPENDS "${CMAKE_BINARY_DIR}/translation-inputs.txt" ${BRQ_WORKSPACE_INPUTS} ${BRQ_GAME_INPUTS} ${BRQ_BUILD_SCRIPTS}
            "${CMAKE_CURRENT_SOURCE_DIR}/data/graphics_templates.json"
            "${BRQ_GUST_EBM_EXECUTABLE}" ${BRQ_GUST_ENC_EXECUTABLE} ${BRQ_RESOLVED_FONT_TTF}
            ${BRQ_MSDF_ATLAS_GEN_EXECUTABLE} ${BRQ_GUST_G1T_EXECUTABLE}
        VERBATIM)
    add_custom_target(brq-assets ALL DEPENDS "${BRQ_TRANSLATION_OUTPUT}/.brq-translation-build")
    set(BRQ_ASSET_DEPENDS "${BRQ_TRANSLATION_OUTPUT}/.brq-translation-build")
    add_custom_target(check-translation
        COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/tools/build_translation.py"
            --check-runtime "${BRQ_EFFECTIVE_RUNTIME_ROOT}"
        DEPENDS brq-assets VERBATIM)
    return()
endif()

set(BRQ_EFFECTIVE_RUNTIME_ROOT "${BRQ_RUNTIME_ROOT}")
set(BRQ_ASSET_DEPENDS)
if(BRQ_RUNTIME_ROOT)
    file(GLOB_RECURSE BRQ_RUNTIME_INPUTS CONFIGURE_DEPENDS LIST_DIRECTORIES FALSE "${BRQ_RUNTIME_ROOT}/*")
    list(APPEND BRQ_ASSET_DEPENDS ${BRQ_RUNTIME_INPUTS})
endif()
file(GLOB BRQ_TRANSLATION_INPUTS CONFIGURE_DEPENDS "${BRQ_TRANSLATIONS_ROOT}/B[0-4].json")
list(APPEND BRQ_ASSET_DEPENDS ${BRQ_TRANSLATION_INPUTS})

if((BRQ_FONT_TTF AND NOT BRQ_EXPORT_LOCALIZATION) OR BRQ_GRAPHICS_ROOT)
    if(NOT IS_DIRECTORY "${BRQ_RUNTIME_ROOT}")
        message(FATAL_ERROR "Font/graphics builds require BRQ_RUNTIME_ROOT with the existing game-ready assets")
    endif()
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    set(BRQ_EFFECTIVE_RUNTIME_ROOT "${CMAKE_BINARY_DIR}/runtime")
    set(BRQ_PREPARE_ARGS
        --runtime-root "${BRQ_RUNTIME_ROOT}" --output "${BRQ_EFFECTIVE_RUNTIME_ROOT}"
        --translations-root "${BRQ_TRANSLATIONS_ROOT}")
    if(BRQ_TARGET_LANGUAGE)
        list(APPEND BRQ_PREPARE_ARGS --target-language "${BRQ_TARGET_LANGUAGE}")
    endif()
    if(BRQ_FONT_TTF)
        if(NOT BRQ_TARGET_LANGUAGE)
            message(FATAL_ERROR "Set BRQ_TARGET_LANGUAGE for font generation")
        endif()
        foreach(tool IN ITEMS MSDF_ATLAS_GEN GUST_G1T)
            if(NOT EXISTS "${BRQ_${tool}_EXECUTABLE}" OR IS_DIRECTORY "${BRQ_${tool}_EXECUTABLE}")
                message(FATAL_ERROR "Set BRQ_${tool}_EXECUTABLE to the native font conversion tool")
            endif()
        endforeach()
        list(APPEND BRQ_PREPARE_ARGS
            --game-root "${BRQ_SOURCE_GAME_ROOT}" --pak-root "${BRQ_PAK_ROOT}" --font "${BRQ_RESOLVED_FONT_TTF}"
            --font-output "${CMAKE_BINARY_DIR}/fonts"
            --msdf-atlas-gen "${BRQ_MSDF_ATLAS_GEN_EXECUTABLE}"
            --gust-g1t "${BRQ_GUST_G1T_EXECUTABLE}")
        foreach(segment IN ITEMS B0 B1 B2 B3 B4)
            list(APPEND BRQ_ASSET_DEPENDS "${BRQ_SOURCE_GAME_ROOT}/${segment}.dll")
        endforeach()
        file(GLOB font_archives CONFIGURE_DEPENDS "${BRQ_PAK_ROOT}/*.PAK" "${BRQ_PAK_ROOT}/*.pak")
        list(APPEND BRQ_ASSET_DEPENDS ${font_archives} tools/build_translation.py tools/pak_source.py)
        list(APPEND BRQ_ASSET_DEPENDS "${BRQ_RESOLVED_FONT_TTF}"
            "${BRQ_MSDF_ATLAS_GEN_EXECUTABLE}" "${BRQ_GUST_G1T_EXECUTABLE}")
    endif()
    if(BRQ_GRAPHICS_ROOT)
        list(APPEND BRQ_PREPARE_ARGS --graphics-root "${BRQ_GRAPHICS_ROOT}")
        file(GLOB_RECURSE graphics_inputs CONFIGURE_DEPENDS LIST_DIRECTORIES FALSE "${BRQ_GRAPHICS_ROOT}/*")
        list(APPEND BRQ_ASSET_DEPENDS ${graphics_inputs})
    endif()
    file(GLOB_RECURSE font_scripts CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tools/fonts/*.py")
    file(GENERATE OUTPUT "${CMAKE_BINARY_DIR}/asset-inputs.txt"
        CONTENT "${BRQ_PREPARE_ARGS}\n${BRQ_ASSET_DEPENDS}\n${font_scripts}\n")
    add_custom_command(OUTPUT "${BRQ_EFFECTIVE_RUNTIME_ROOT}/.brq-prepared-runtime"
        COMMAND "${Python3_EXECUTABLE}" -B "${CMAKE_CURRENT_SOURCE_DIR}/tools/prepare_runtime.py" ${BRQ_PREPARE_ARGS}
        DEPENDS "${CMAKE_BINARY_DIR}/asset-inputs.txt" ${BRQ_ASSET_DEPENDS} ${font_scripts}
            tools/prepare_runtime.py tools/prepare_graphics.py tools/generate_embedded.py tools/translation_table.py
        VERBATIM)
    add_custom_target(brq-assets ALL DEPENDS "${BRQ_EFFECTIVE_RUNTIME_ROOT}/.brq-prepared-runtime")
    set(BRQ_ASSET_DEPENDS "${BRQ_EFFECTIVE_RUNTIME_ROOT}/.brq-prepared-runtime" ${BRQ_TRANSLATION_INPUTS})
endif()
