# Warning policy for OUR C in the ESP-IDF build (#154), equal to the host build's
# -Wall -Wextra -Werror (firmware/host/Makefile OWN_OBJ). ESP-IDF's global flags weaken -Wextra
# (-Wno-error=extra, -Wno-unused-parameter, -Wno-sign-compare, -Wno-enum-conversion, and
# -Wno-error= for unused/deprecated), and CMake de-duplicates a plain "-Wall -Wextra -Werror" into
# those earlier global flags, so re-enable each one explicitly. Target options come after the
# global ones, so these win. Usage, in a component's CMakeLists.txt after idf_component_register:
#   include(${CMAKE_CURRENT_LIST_DIR}/<up>/cmake/cali_strict.cmake)
target_compile_options(${COMPONENT_LIB} PRIVATE
    -Werror=all -Werror=extra
    -Wunused-parameter -Wsign-compare -Wenum-conversion
    -Werror=unused-parameter -Werror=sign-compare -Werror=enum-conversion
    -Werror=unused-function -Werror=unused-variable -Werror=unused-but-set-variable
    -Werror=deprecated-declarations)
