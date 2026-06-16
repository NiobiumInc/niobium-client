// DSL bridge: provides mlp(cc, ct) for extern_call("mlp", ...) in server.niob.
// Loads HEIR v2 weights at first call and delegates to mnist() from mlp_openfhe.cpp.
// Requires ML_WEIGHT_DIR env var pointing to submission/data/.
#include "mlp_openfhe.h"
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

static std::string weight_dir() {
    const char* e = getenv("ML_WEIGHT_DIR");
    if (!e) throw std::runtime_error(
        "mlp_bridge: ML_WEIGHT_DIR is not set. "
        "Export ML_WEIGHT_DIR=<path-to-submission/data> before running.");
    return std::string(e);
}

static std::vector<float> load_bin(const std::string& path, size_t count) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("mlp_bridge: cannot open " + path);
    std::vector<float> v(count);
    f.read(reinterpret_cast<char*>(v.data()), count * sizeof(float));
    if (static_cast<size_t>(f.gcount()) != count * sizeof(float))
        throw std::runtime_error("mlp_bridge: short read in " + path);
    return v;
}

ConstCiphertext<DCRTPoly> mlp(CryptoContext<DCRTPoly> cc,
                               ConstCiphertext<DCRTPoly> ct) {
    static const std::string wdir    = weight_dir();
    static const auto fc1_weight     = load_bin(wdir + "/fc1_weight.bin", 512 * 784);
    static const auto fc1_bias       = load_bin(wdir + "/fc1_bias.bin", 512);
    static const auto fc2_weight     = load_bin(wdir + "/fc2_weight.bin", 10 * 512);
    static const auto fc2_bias       = load_bin(wdir + "/fc2_bias.bin", 10);

    // mnist() requires non-const Ciphertext due to OpenFHE API design, but does not
    // modify the input — all internal ops (EvalAdd, EvalMult, EvalAtIndex) create new ciphertexts.
    auto mct = std::const_pointer_cast<CiphertextImpl<DCRTPoly>>(ct);
    auto result = mnist(cc, fc1_weight, fc1_bias, fc2_weight, fc2_bias, {mct});
    return result[0];
}
