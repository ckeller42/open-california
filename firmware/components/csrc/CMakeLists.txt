# The generated frame codec from the repo's csrc/ (one dictionary, two consumers: #156). Read-only
# firmware: CODEC_NO_ENCODE is PUBLIC, so codec_encode is neither compiled nor declared anywhere in
# this build (the top-level CMakeLists.txt also fails the link if the symbol appears).
set(csrc_dir "${CMAKE_CURRENT_LIST_DIR}/../../../csrc")
idf_component_register(SRCS "${csrc_dir}/codec.c"
                       INCLUDE_DIRS "${csrc_dir}")
target_compile_definitions(${COMPONENT_LIB} PUBLIC CODEC_NO_ENCODE)
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
