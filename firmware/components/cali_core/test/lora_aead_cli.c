/* lora_aead_cli.c — lora_setup_cli.c with the real PSA open instead of the XOR stub (same commands).
 * Driven by tests/firmware/test_lora_aead.py. */
#define CALI_LORA_REAL_AEAD
#include "lora_setup_cli.c"
