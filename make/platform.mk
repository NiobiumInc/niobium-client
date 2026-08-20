# Host detection, shared by the root Makefile and dsl_fhe/Makefile so the two
# cannot drift apart.
#
# getconf is in /usr/bin on both Linux and macOS and needs no coreutils, unlike
# nproc, and no /usr/sbin on PATH, unlike sysctl. The literal fallback matters:
# an empty value would turn `-j $(NB_NUM_CPUS)` into unbounded parallelism.

NB_NUM_CPUS := $(shell getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
