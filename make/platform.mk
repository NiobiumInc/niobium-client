# Host detection, shared by the root Makefile and dsl_fhe/Makefile so the two
# cannot drift apart.
#
# nproc comes first because it is the only one of these that honours the CPU
# affinity mask: it counts the bits from sched_getaffinity, so a container or a
# Slurm allocation gets the count it was actually given, while getconf reads
# /sys and reports the whole host. getconf then covers macOS, where nproc is
# absent, and needs neither coreutils nor /usr/sbin on PATH, unlike sysctl. The
# literal fallback matters: an empty value would turn `-j $(NB_NUM_CPUS)` into
# unbounded parallelism.

NB_NUM_CPUS := $(shell nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)
