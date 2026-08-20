# Host detection, shared by the root Makefile and dsl_fhe/Makefile so the two
# cannot drift apart.
#
# nproc is coreutils and absent on macOS; sysctl lives in /usr/sbin, which is not
# always on PATH, so it is also tried by absolute path. Falling back to a literal
# matters: an empty value would turn `-j $(NB_NUM_CPUS)` into unbounded
# parallelism rather than an error.

NB_NUM_CPUS := $(shell nproc 2>/dev/null \
	|| sysctl -n hw.ncpu 2>/dev/null \
	|| /usr/sbin/sysctl -n hw.ncpu 2>/dev/null \
	|| echo 4)
