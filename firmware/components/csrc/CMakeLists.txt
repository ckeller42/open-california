# The generated frame codec from the repo's csrc/ (one dictionary, two consumers: #156); codec_encode
# is used by cali_core/control.c (#154 B).
set(csrc_dir "${CMAKE_CURRENT_LIST_DIR}/../../../csrc")
idf_component_register(SRCS "${csrc_dir}/codec.c"
                       INCLUDE_DIRS "${csrc_dir}")
include(${CMAKE_CURRENT_LIST_DIR}/../../cmake/cali_strict.cmake)
