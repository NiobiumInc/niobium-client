// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// FHETCH Polynomial IR API — client-side implementation.
//
// Each FHETCH instruction function records one operation into the trace
// via the TraceWriter. The server-side niobium-compiler parses the FHETCH
// trace and lowers it to internal hardware instructions.

#include "niobium/fhetch_api.h"
#include "niobium/compiler.h"
#include "compiler_internal.h"

#include <algorithm>
#include <atomic>
#include <cassert>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>

namespace niobium::fhetch {

// ============================================================================
// Synthetic address allocator
// ============================================================================

static std::atomic<uintptr_t> next_address_{0};

static uintptr_t alloc_address() {
    return next_address_.fetch_add(1, std::memory_order_relaxed);
}

// ============================================================================
// Helper: emit a FHETCH instruction to the trace
// ============================================================================

static void emit(const std::string& instruction) {
    niobium::detail::trace_writer().emit(instruction);
}

// Format a modulus as hex for the trace
static std::string qhex(uint64_t q) {
    std::ostringstream ss;
    ss << "0x" << std::hex << std::uppercase << q;
    return ss.str();
}

// Format an address as %N
static std::string addr(uintptr_t a) {
    return "%" + std::to_string(a);
}

// ============================================================================
// Probe and input registries
// ============================================================================

struct ProbeEntry {
    std::string name;
    enum Kind { POLY, MRP_KIND, MRPA, SRPA } kind;
    Polynomial poly;
    MRP mrp;
    MRPArray mrpa;
    SRPArray srpa;
};

static std::vector<ProbeEntry>& probe_registry() {
    static std::vector<ProbeEntry> registry;
    return registry;
}

struct InputEntry {
    std::string name;
    enum Kind { POLY, MRP_KIND, MRPA, SRPA } kind;
    Polynomial poly;
    MRP mrp;
    MRPArray mrpa;
    SRPArray srpa;
};

static std::vector<InputEntry>& input_registry() {
    static std::vector<InputEntry> registry;
    return registry;
}

// ============================================================================
// Impl structs
// ============================================================================

struct PolynomialImpl {
    uintptr_t address;
    uint64_t ring_dim;
    Format fmt;
    NumberType ntype;
    std::vector<uint64_t> int_data;
    std::vector<double> fp_data;

    PolynomialImpl(uint64_t rd, Format f, NumberType nt)
        : address(alloc_address()), ring_dim(rd), fmt(f), ntype(nt) {}
};

struct ScalarImpl {
    uintptr_t address;
    NumberType ntype;
    uint64_t int_value;
    double fp_value;

    ScalarImpl(NumberType nt, uint64_t iv, double fv)
        : address(alloc_address()), ntype(nt), int_value(iv), fp_value(fv) {}
};

struct SRPArrayImpl {
    std::vector<Polynomial> elements;
};

struct MRPImpl {
    ModuliBase base_;
    std::map<uint64_t, Polynomial> residues_;
    uint64_t ring_dim_;

    MRPImpl() : ring_dim_(0) {}
    MRPImpl(const ModuliBase& b, uint64_t rd) : base_(b), ring_dim_(rd) {
        for (auto q : b)
            residues_[q] = Polynomial::zeros(rd);
    }
};

struct MRSImpl {
    ModuliBase base_;
    std::map<uint64_t, Scalar> scalars_;

    MRSImpl() = default;
    explicit MRSImpl(const ModuliBase& b) : base_(b) {
        for (auto q : b)
            scalars_[q] = Scalar::from_int(0);
    }
};

struct MRPArrayImpl {
    std::vector<MRP> elements;
};

// ============================================================================
// Helper: create result polynomial from a template
// ============================================================================

static Polynomial make_result(const Polynomial& from, Format fmt) {
    if (from.number_type() == NumberType::NonInteger)
        return Polynomial::zeros_ni(from.ring_dimension(), fmt);
    return Polynomial::zeros(from.ring_dimension(), fmt);
}

static Polynomial make_result(const Polynomial& from) {
    return make_result(from, from.format());
}

// ============================================================================
// Polynomial
// ============================================================================

Polynomial::Polynomial() = default;
Polynomial::~Polynomial() = default;
Polynomial::Polynomial(const Polynomial&) = default;
Polynomial& Polynomial::operator=(const Polynomial&) = default;
Polynomial::Polynomial(Polynomial&&) noexcept = default;
Polynomial& Polynomial::operator=(Polynomial&&) noexcept = default;
Polynomial::Polynomial(std::shared_ptr<PolynomialImpl> p) : impl_(std::move(p)) {}

Polynomial Polynomial::zeros(uint64_t ring_dim, Format fmt) {
    return Polynomial(std::make_shared<PolynomialImpl>(ring_dim, fmt, NumberType::Integer));
}

Polynomial Polynomial::zeros_ni(uint64_t ring_dim, Format fmt) {
    return Polynomial(std::make_shared<PolynomialImpl>(ring_dim, fmt, NumberType::NonInteger));
}

Polynomial Polynomial::from_data(const std::vector<uint64_t>& components,
                                 uint64_t ring_dim, Format fmt) {
    auto p = std::make_shared<PolynomialImpl>(ring_dim, fmt, NumberType::Integer);
    p->int_data = components;
    return Polynomial(p);
}

Polynomial Polynomial::from_data_ni(const std::vector<double>& components,
                                    uint64_t ring_dim, Format fmt) {
    auto p = std::make_shared<PolynomialImpl>(ring_dim, fmt, NumberType::NonInteger);
    p->fp_data = components;
    return Polynomial(p);
}

uint64_t Polynomial::ring_dimension() const { return impl_ ? impl_->ring_dim : 0; }
NumberType Polynomial::number_type() const { return impl_ ? impl_->ntype : NumberType::Integer; }
Format Polynomial::format() const { return impl_ ? impl_->fmt : Format::Evaluation; }
bool Polynomial::is_valid() const { return impl_ != nullptr; }
PolynomialImpl* Polynomial::impl() const { return impl_.get(); }

// ============================================================================
// Scalar
// ============================================================================

Scalar::Scalar() = default;
Scalar::~Scalar() = default;
Scalar::Scalar(const Scalar&) = default;
Scalar& Scalar::operator=(const Scalar&) = default;
Scalar::Scalar(Scalar&&) noexcept = default;
Scalar& Scalar::operator=(Scalar&&) noexcept = default;
Scalar::Scalar(std::shared_ptr<ScalarImpl> p) : impl_(std::move(p)) {}

Scalar Scalar::from_int(uint64_t value) {
    return Scalar(std::make_shared<ScalarImpl>(NumberType::Integer, value, 0.0));
}

Scalar Scalar::from_double(double value) {
    return Scalar(std::make_shared<ScalarImpl>(NumberType::NonInteger, 0, value));
}

NumberType Scalar::number_type() const { return impl_ ? impl_->ntype : NumberType::Integer; }
bool Scalar::is_valid() const { return impl_ != nullptr; }
ScalarImpl* Scalar::impl() const { return impl_.get(); }

// ============================================================================
// SRPArray
// ============================================================================

SRPArray::SRPArray() : impl_(std::make_shared<SRPArrayImpl>()) {}
SRPArray::~SRPArray() = default;
SRPArray::SRPArray(const SRPArray&) = default;
SRPArray& SRPArray::operator=(const SRPArray&) = default;
SRPArray::SRPArray(SRPArray&&) noexcept = default;
SRPArray& SRPArray::operator=(SRPArray&&) noexcept = default;
SRPArray::SRPArray(std::shared_ptr<SRPArrayImpl> p) : impl_(std::move(p)) {}

SRPArray::SRPArray(size_t n) : impl_(std::make_shared<SRPArrayImpl>()) {
    impl_->elements.resize(n);
}

SRPArray::SRPArray(std::initializer_list<Polynomial> polys)
    : impl_(std::make_shared<SRPArrayImpl>()) {
    impl_->elements.assign(polys.begin(), polys.end());
}

size_t SRPArray::length() const { return impl_ ? impl_->elements.size() : 0; }

Polynomial& SRPArray::operator[](size_t i) {
    if (!impl_ || i >= impl_->elements.size())
        throw std::out_of_range("SRPArray index out of range");
    return impl_->elements[i];
}

const Polynomial& SRPArray::operator[](size_t i) const {
    if (!impl_ || i >= impl_->elements.size())
        throw std::out_of_range("SRPArray index out of range");
    return impl_->elements[i];
}

void SRPArray::append(const Polynomial& p) {
    if (impl_) impl_->elements.push_back(p);
}

bool SRPArray::is_valid() const { return impl_ != nullptr; }
SRPArrayImpl* SRPArray::impl() const { return impl_.get(); }

// ============================================================================
// MRP
// ============================================================================

MRP::MRP() : impl_(std::make_shared<MRPImpl>()) {}
MRP::~MRP() = default;
MRP::MRP(const MRP&) = default;
MRP& MRP::operator=(const MRP&) = default;
MRP::MRP(MRP&&) noexcept = default;
MRP& MRP::operator=(MRP&&) noexcept = default;
MRP::MRP(std::shared_ptr<MRPImpl> p) : impl_(std::move(p)) {}

MRP::MRP(const ModuliBase& base, uint64_t ring_dim)
    : impl_(std::make_shared<MRPImpl>(base, ring_dim)) {}

MRP MRP::from_pairs(const std::vector<std::pair<Polynomial, uint64_t>>& pairs) {
    auto impl = std::make_shared<MRPImpl>();
    for (auto& [poly, q] : pairs) {
        impl->base_.push_back(q);
        impl->residues_[q] = poly;
    }
    if (!pairs.empty() && pairs[0].first.is_valid())
        impl->ring_dim_ = pairs[0].first.ring_dimension();
    return MRP(impl);
}

const ModuliBase& MRP::base() const {
    static const ModuliBase empty;
    return impl_ ? impl_->base_ : empty;
}

size_t MRP::num_residues() const { return impl_ ? impl_->base_.size() : 0; }

Polynomial& MRP::operator[](uint64_t q) {
    if (!impl_) throw std::out_of_range("MRP is empty");
    auto it = impl_->residues_.find(q);
    if (it == impl_->residues_.end())
        throw std::out_of_range("Modulus not in MRP base");
    return it->second;
}

const Polynomial& MRP::operator[](uint64_t q) const {
    if (!impl_) throw std::out_of_range("MRP is empty");
    auto it = impl_->residues_.find(q);
    if (it == impl_->residues_.end())
        throw std::out_of_range("Modulus not in MRP base");
    return it->second;
}

bool MRP::is_valid() const { return impl_ != nullptr && !impl_->base_.empty(); }
MRPImpl* MRP::impl() const { return impl_.get(); }

// ============================================================================
// MRS
// ============================================================================

MRS::MRS() : impl_(std::make_shared<MRSImpl>()) {}
MRS::~MRS() = default;
MRS::MRS(const MRS&) = default;
MRS& MRS::operator=(const MRS&) = default;
MRS::MRS(MRS&&) noexcept = default;
MRS& MRS::operator=(MRS&&) noexcept = default;
MRS::MRS(std::shared_ptr<MRSImpl> p) : impl_(std::move(p)) {}

MRS::MRS(const ModuliBase& base) : impl_(std::make_shared<MRSImpl>(base)) {}

MRS MRS::from_pairs(const std::vector<std::pair<Scalar, uint64_t>>& pairs) {
    auto impl = std::make_shared<MRSImpl>();
    for (auto& [s, q] : pairs) {
        impl->base_.push_back(q);
        impl->scalars_[q] = s;
    }
    return MRS(impl);
}

const ModuliBase& MRS::base() const {
    static const ModuliBase empty;
    return impl_ ? impl_->base_ : empty;
}

size_t MRS::num_residues() const { return impl_ ? impl_->base_.size() : 0; }

Scalar& MRS::operator[](uint64_t q) {
    if (!impl_) throw std::out_of_range("MRS is empty");
    auto it = impl_->scalars_.find(q);
    if (it == impl_->scalars_.end())
        throw std::out_of_range("Modulus not in MRS base");
    return it->second;
}

const Scalar& MRS::operator[](uint64_t q) const {
    if (!impl_) throw std::out_of_range("MRS is empty");
    auto it = impl_->scalars_.find(q);
    if (it == impl_->scalars_.end())
        throw std::out_of_range("Modulus not in MRS base");
    return it->second;
}

bool MRS::is_valid() const { return impl_ != nullptr && !impl_->base_.empty(); }
MRSImpl* MRS::impl() const { return impl_.get(); }

// ============================================================================
// MRPArray
// ============================================================================

MRPArray::MRPArray() : impl_(std::make_shared<MRPArrayImpl>()) {}
MRPArray::~MRPArray() = default;
MRPArray::MRPArray(const MRPArray&) = default;
MRPArray& MRPArray::operator=(const MRPArray&) = default;
MRPArray::MRPArray(MRPArray&&) noexcept = default;
MRPArray& MRPArray::operator=(MRPArray&&) noexcept = default;
MRPArray::MRPArray(std::shared_ptr<MRPArrayImpl> p) : impl_(std::move(p)) {}

MRPArray::MRPArray(size_t n) : impl_(std::make_shared<MRPArrayImpl>()) {
    impl_->elements.resize(n);
}

MRPArray::MRPArray(std::initializer_list<MRP> mrps)
    : impl_(std::make_shared<MRPArrayImpl>()) {
    impl_->elements.assign(mrps.begin(), mrps.end());
}

size_t MRPArray::length() const { return impl_ ? impl_->elements.size() : 0; }

MRP& MRPArray::operator[](size_t i) {
    if (!impl_ || i >= impl_->elements.size())
        throw std::out_of_range("MRPArray index out of range");
    return impl_->elements[i];
}

const MRP& MRPArray::operator[](size_t i) const {
    if (!impl_ || i >= impl_->elements.size())
        throw std::out_of_range("MRPArray index out of range");
    return impl_->elements[i];
}

void MRPArray::append(const MRP& m) {
    if (impl_) impl_->elements.push_back(m);
}

bool MRPArray::is_valid() const { return impl_ != nullptr; }
MRPArrayImpl* MRPArray::impl() const { return impl_.get(); }

// ============================================================================
// Epoch reset
// ============================================================================

void reset_for_epoch() {
    next_address_.store(0, std::memory_order_relaxed);
    input_registry().clear();
    probe_registry().clear();
}

// ============================================================================
// INPUT / OUTPUT TAGGING
// ============================================================================

void tag_input(const std::string& name, const Polynomial& p) {
    InputEntry e; e.name = name; e.kind = InputEntry::POLY; e.poly = p;
    input_registry().push_back(std::move(e));
}

void tag_input(const std::string& name, const MRP& m) {
    InputEntry e; e.name = name; e.kind = InputEntry::MRP_KIND; e.mrp = m;
    input_registry().push_back(std::move(e));
}

void tag_input(const std::string& name, const MRPArray& arr) {
    InputEntry e; e.name = name; e.kind = InputEntry::MRPA; e.mrpa = arr;
    input_registry().push_back(std::move(e));
}

void tag_input(const std::string& name, const SRPArray& arr) {
    InputEntry e; e.name = name; e.kind = InputEntry::SRPA; e.srpa = arr;
    input_registry().push_back(std::move(e));
}

void tag_output(const std::string& name, const Polynomial& p) {
    emit("# output " + name + " " + addr(p.impl()->address));
    ProbeEntry e; e.name = name; e.kind = ProbeEntry::POLY; e.poly = p;
    probe_registry().push_back(std::move(e));
}

void tag_output(const std::string& name, const MRP& m) {
    for (auto q : m.base())
        emit("# output " + name + " " + addr(m[q].impl()->address));
    ProbeEntry e; e.name = name; e.kind = ProbeEntry::MRP_KIND; e.mrp = m;
    probe_registry().push_back(std::move(e));
}

void tag_output(const std::string& name, const MRPArray& arr) {
    ProbeEntry e; e.name = name; e.kind = ProbeEntry::MRPA; e.mrpa = arr;
    probe_registry().push_back(std::move(e));
}

void tag_output(const std::string& name, const SRPArray& arr) {
    ProbeEntry e; e.name = name; e.kind = ProbeEntry::SRPA; e.srpa = arr;
    probe_registry().push_back(std::move(e));
}

uint64_t get_input_ring_dimension() {
    for (const auto& input : input_registry()) {
        switch (input.kind) {
        case InputEntry::POLY:
            if (input.poly.is_valid() && input.poly.ring_dimension() > 0)
                return input.poly.ring_dimension();
            break;
        case InputEntry::MRP_KIND:
            if (input.mrp.is_valid() && !input.mrp.base().empty()) {
                const auto& p = input.mrp[input.mrp.base()[0]];
                if (p.is_valid() && p.ring_dimension() > 0)
                    return p.ring_dimension();
            }
            break;
        case InputEntry::MRPA:
            if (input.mrpa.length() > 0 && input.mrpa[0].is_valid()) {
                const auto& b = input.mrpa[0].base();
                if (!b.empty()) return input.mrpa[0][b[0]].ring_dimension();
            }
            break;
        case InputEntry::SRPA:
            if (input.srpa.length() > 0 && input.srpa[0].is_valid())
                return input.srpa[0].ring_dimension();
            break;
        }
    }
    return 0;
}

void save_input_data() {
    // TODO: Serialize input polynomial data to JSON for server consumption
    auto& registry = input_registry();
    if (registry.empty()) return;
    std::cout << "[FHETCH] " << registry.size() << " inputs registered" << std::endl;
}

void save_probe_outputs() {
    // TODO: Serialize output probe metadata to JSON for server consumption
    auto& registry = probe_registry();
    if (registry.empty()) return;
    std::cout << "[FHETCH] " << registry.size() << " outputs registered" << std::endl;
}

// ============================================================================
// BASELINE INSTRUCTIONS
// ============================================================================

Polynomial sr_addp(const Polynomial& a, const Polynomial& b, uint64_t q) {
    auto result = make_result(a);
    emit("sr_addp " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + addr(b.impl()->address) +
         ", q=" + qhex(q));
    return result;
}

Polynomial sr_addps(const Polynomial& a, const Scalar& s, uint64_t q) {
    auto result = make_result(a);
    emit("sr_addps " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " +
         std::to_string(s.impl()->int_value) + ", q=" + qhex(q));
    return result;
}

Polynomial sr_addps_coeff(const Polynomial& a, const Scalar& s, uint64_t q) {
    auto result = make_result(a, Format::Coefficient);
    emit("sr_addps_coeff " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " +
         std::to_string(s.impl()->int_value) + ", q=" + qhex(q));
    return result;
}

Polynomial sr_negp(const Polynomial& a, uint64_t q) {
    auto result = make_result(a);
    emit("sr_negp " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", q=" + qhex(q));
    return result;
}

Polynomial sr_subp(const Polynomial& a, const Polynomial& b, uint64_t q) {
    auto result = make_result(a);
    emit("sr_subp " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + addr(b.impl()->address) +
         ", q=" + qhex(q));
    return result;
}

Polynomial sr_subps(const Polynomial& a, const Scalar& s, uint64_t q) {
    auto result = make_result(a);
    emit("sr_subps " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " +
         std::to_string(s.impl()->int_value) + ", q=" + qhex(q));
    return result;
}

Polynomial sr_subps_coeff(const Polynomial& a, const Scalar& s, uint64_t q) {
    auto result = make_result(a, Format::Coefficient);
    emit("sr_subps_coeff " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " +
         std::to_string(s.impl()->int_value) + ", q=" + qhex(q));
    return result;
}

Polynomial sr_mulp(const Polynomial& a, const Polynomial& b, uint64_t q) {
    auto result = make_result(a);
    emit("sr_mulp " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + addr(b.impl()->address) +
         ", q=" + qhex(q));
    return result;
}

Polynomial sr_mulps(const Polynomial& a, const Scalar& s, uint64_t q) {
    auto result = make_result(a);
    emit("sr_mulps " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " +
         std::to_string(s.impl()->int_value) + ", q=" + qhex(q));
    return result;
}

Polynomial sr_ntt(const Polynomial& a, uint64_t q) {
    auto result = make_result(a, Format::Evaluation);
    emit("sr_ntt " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", q=" + qhex(q));
    return result;
}

Polynomial sr_intt(const Polynomial& a, uint64_t q) {
    auto result = make_result(a, Format::Coefficient);
    emit("sr_intt " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", q=" + qhex(q));
    return result;
}

Polynomial sr_permute(const Polynomial& a,
                      const std::vector<uint64_t>& /*srcs*/,
                      const std::vector<int>& /*signs*/,
                      uint64_t q) {
    auto result = make_result(a);
    emit("sr_permute " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", q=" + qhex(q));
    return result;
}

void halt() {
    emit("halt");
}

// ============================================================================
// OPTIONAL OPERATIONS — non-integer arithmetic
// ============================================================================

Polynomial sr_addp_ni(const Polynomial& a, const Polynomial& b) {
    auto result = make_result(a);
    emit("sr_addp_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + addr(b.impl()->address));
    return result;
}

Polynomial sr_addps_ni(const Polynomial& a, const Scalar& s) {
    auto result = make_result(a);
    emit("sr_addps_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + std::to_string(s.impl()->fp_value));
    return result;
}

Polynomial sr_addps_coeff_ni(const Polynomial& a, const Scalar& s) {
    auto result = make_result(a, Format::Coefficient);
    emit("sr_addps_coeff_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + std::to_string(s.impl()->fp_value));
    return result;
}

Polynomial sr_negp_ni(const Polynomial& a) {
    auto result = make_result(a);
    emit("sr_negp_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address));
    return result;
}

Polynomial sr_subp_ni(const Polynomial& a, const Polynomial& b) {
    auto result = make_result(a);
    emit("sr_subp_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + addr(b.impl()->address));
    return result;
}

Polynomial sr_subps_ni(const Polynomial& a, const Scalar& s) {
    auto result = make_result(a);
    emit("sr_subps_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + std::to_string(s.impl()->fp_value));
    return result;
}

Polynomial sr_subps_coeff_ni(const Polynomial& a, const Scalar& s) {
    auto result = make_result(a, Format::Coefficient);
    emit("sr_subps_coeff_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + std::to_string(s.impl()->fp_value));
    return result;
}

Polynomial sr_mulp_ni(const Polynomial& a, const Polynomial& b) {
    auto result = make_result(a);
    emit("sr_mulp_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + addr(b.impl()->address));
    return result;
}

Polynomial sr_mulps_ni(const Polynomial& a, const Scalar& s) {
    auto result = make_result(a);
    emit("sr_mulps_ni " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address) + ", " + std::to_string(s.impl()->fp_value));
    return result;
}

// ============================================================================
// OPTIONAL OPERATIONS — Fourier transforms
// ============================================================================

Polynomial sr_ft(const Polynomial& a) {
    auto result = make_result(a, Format::Evaluation);
    emit("sr_ft " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address));
    return result;
}

Polynomial sr_ift(const Polynomial& a) {
    auto result = make_result(a, Format::Coefficient);
    emit("sr_ift " + addr(result.impl()->address) + ", " +
         addr(a.impl()->address));
    return result;
}

// ============================================================================
// OPTIONAL OPERATIONS — coefficient access
// ============================================================================

Scalar sr_coeff_extract(const Polynomial& /*p*/, uint64_t /*i*/) {
    return Scalar::from_int(0);  // TODO
}

Polynomial sr_coeff_assign(const Polynomial& p, uint64_t /*i*/, const Scalar& /*val*/) {
    return make_result(p);  // TODO
}

// ============================================================================
// OPTIONAL OPERATIONS — torus and sample operations
// ============================================================================

Polynomial sr_torus_mod_reduce(const Polynomial& p, double /*c*/) {
    return make_result(p);  // TODO
}

std::vector<uint64_t> sr_sample_extract(const SRPArray& /*rlwe*/, uint64_t lwe_dim) {
    return std::vector<uint64_t>(lwe_dim + 1, 0);  // TODO
}

// ============================================================================
// GADGETS — Polynomial level
// ============================================================================

Polynomial sr_automorph_eval(const Polynomial& x, uint64_t k) {
    auto result = make_result(x);
    emit("sr_automorph_eval " + addr(result.impl()->address) + ", " +
         addr(x.impl()->address) + ", k=" + std::to_string(k));
    return result;
}

Polynomial sr_automorph_coeff(const Polynomial& x, uint64_t k, uint64_t q) {
    auto result = make_result(x, Format::Coefficient);
    emit("sr_automorph_coeff " + addr(result.impl()->address) + ", " +
         addr(x.impl()->address) + ", k=" + std::to_string(k) +
         ", q=" + qhex(q));
    return result;
}

Polynomial sr_rot_automorph_coeff(const Polynomial& x, uint64_t offset, uint64_t q) {
    auto result = make_result(x, Format::Coefficient);
    emit("sr_rot_automorph_coeff " + addr(result.impl()->address) + ", " +
         addr(x.impl()->address) + ", offset=" + std::to_string(offset) +
         ", q=" + qhex(q));
    return result;
}

SRPArray sr_batch_ft(const SRPArray& x) {
    SRPArray y(x.length());
    for (size_t i = 0; i < x.length(); ++i)
        y[i] = sr_ft(x[i]);
    return y;
}

SRPArray sr_batch_ift(const SRPArray& x) {
    SRPArray y(x.length());
    for (size_t i = 0; i < x.length(); ++i)
        y[i] = sr_ift(x[i]);
    return y;
}

// ============================================================================
// GADGETS — Multi-Residue basic arithmetic
// ============================================================================

MRP mr_addp(const MRP& x, const MRP& y) {
    const auto& base = x.base();
    MRP z(base, x[base[0]].ring_dimension());
    for (auto q : base) z[q] = sr_addp(x[q], y[q], q);
    return z;
}

MRP mr_subp(const MRP& x, const MRP& y) {
    const auto& base = x.base();
    MRP z(base, x[base[0]].ring_dimension());
    for (auto q : base) z[q] = sr_subp(x[q], y[q], q);
    return z;
}

MRP mr_mulp(const MRP& x, const MRP& y) {
    const auto& base = x.base();
    MRP z(base, x[base[0]].ring_dimension());
    for (auto q : base) z[q] = sr_mulp(x[q], y[q], q);
    return z;
}

MRP mr_mulps(const MRP& x, const MRS& s) {
    const auto& base = x.base();
    MRP z(base, x[base[0]].ring_dimension());
    for (auto q : base) z[q] = sr_mulps(x[q], s[q], q);
    return z;
}

MRP mr_addps(const MRP& x, const MRS& s) {
    const auto& base = x.base();
    MRP z(base, x[base[0]].ring_dimension());
    for (auto q : base) z[q] = sr_addps(x[q], s[q], q);
    return z;
}

MRP mr_ntt(const MRP& x) {
    const auto& base = x.base();
    MRP z(base, x[base[0]].ring_dimension());
    for (auto q : base) z[q] = sr_ntt(x[q], q);
    return z;
}

MRP mr_intt(const MRP& x) {
    const auto& base = x.base();
    MRP z(base, x[base[0]].ring_dimension());
    for (auto q : base) z[q] = sr_intt(x[q], q);
    return z;
}

MRP mr_zeros(const ModuliBase& target_base, uint64_t ring_dim) {
    return MRP(target_base, ring_dim);
}

// ============================================================================
// GADGETS — MRP residue manipulation
// ============================================================================

MRP mr_append_srp(const MRP& x, const Polynomial& a, uint64_t q_a) {
    ModuliBase new_base = x.base();
    new_base.push_back(q_a);
    auto impl = std::make_shared<MRPImpl>();
    impl->base_ = new_base;
    impl->ring_dim_ = a.ring_dimension();
    for (auto q : x.base()) impl->residues_[q] = x[q];
    impl->residues_[q_a] = a;
    return MRP(impl);
}

MRP mr_union(const MRP& x, const MRP& y) {
    ModuliBase new_base = x.base();
    for (auto q : y.base()) new_base.push_back(q);
    auto impl = std::make_shared<MRPImpl>();
    impl->base_ = new_base;
    impl->ring_dim_ = x.impl()->ring_dim_;
    for (auto q : x.base()) impl->residues_[q] = x[q];
    for (auto q : y.base()) impl->residues_[q] = y[q];
    return MRP(impl);
}

MRP mr_subset(const MRP& x, const ModuliBase& subbase) {
    auto impl = std::make_shared<MRPImpl>();
    impl->base_ = subbase;
    impl->ring_dim_ = x.impl()->ring_dim_;
    for (auto q : subbase) impl->residues_[q] = x[q];
    return MRP(impl);
}

// ============================================================================
// GADGETS — Fast Base Conversion and CKKS Rescale
// ============================================================================

MRP fast_base_convert(const MRP& x, const ModuliBase& target_base) {
    uint64_t ring_dim = x.impl()->ring_dim_;
    MRP z(target_base, ring_dim);
    for (auto q : x.base()) {
        auto scaled = x[q];
        for (auto p : target_base) {
            auto temp = sr_mulps(scaled, Scalar::from_int(1), p);
            z[p] = sr_addp(z[p], temp, p);
        }
    }
    return z;
}

MRP rescale_fbc(const MRP& x, const ModuliBase& rescale_base) {
    ModuliBase target_base;
    for (auto q : x.base()) {
        if (std::find(rescale_base.begin(), rescale_base.end(), q) == rescale_base.end())
            target_base.push_back(q);
    }
    MRP y = fast_base_convert(x, target_base);
    uint64_t ring_dim = x.impl()->ring_dim_;
    MRP z(target_base, ring_dim);
    for (auto q : target_base) {
        auto temp = sr_subp(x[q], y[q], q);
        z[q] = sr_mulps(temp, Scalar::from_int(1), q);
    }
    return z;
}

// ============================================================================
// GADGETS — MRP Array operations
// ============================================================================

MRP mrpa_dotproduct(const MRPArray& x, const MRPArray& y) {
    assert(x.length() == y.length() && x.length() > 0);
    MRP z = mr_mulp(x[0], y[0]);
    for (size_t i = 1; i < x.length(); ++i) {
        MRP temp = mr_mulp(x[i], y[i]);
        z = mr_addp(z, temp);
    }
    return z;
}

// ============================================================================
// GADGETS — Decomposition
// ============================================================================

MRPArray dig_decomp(const MRP& x,
                     const std::vector<ModuliBase>& digit_bases,
                     const ModuliBase& p_base) {
    ModuliBase target_base = x.base();
    for (auto p : p_base) target_base.push_back(p);
    size_t d = digit_bases.size();
    MRPArray z(d);
    for (size_t i = 0; i < d; ++i) {
        MRP temp = mr_subset(x, digit_bases[i]);
        z[i] = fast_base_convert(temp, target_base);
    }
    return z;
}

SRPArray gadget_decomp(const Polynomial& x, uint64_t /*base*/, uint64_t n_levels) {
    SRPArray z(n_levels);
    for (size_t i = 0; i < n_levels; ++i) z[i] = make_result(x);
    return z;  // TODO: full implementation needs shift instructions
}

SRPArray gadget_decomp_pow2(const Polynomial& x, uint64_t /*log_base*/, uint64_t n_levels) {
    SRPArray z(n_levels);
    for (size_t i = 0; i < n_levels; ++i) z[i] = make_result(x);
    return z;  // TODO
}

// ============================================================================
// GADGETS — GSW/RLWE External Product
// ============================================================================

SRPArray gsw_rlwe_ext_prod(const SRPArray& gsw,
                           const SRPArray& rlwe_in,
                           uint64_t l, uint64_t base) {
    SRPArray decomp0 = gadget_decomp(rlwe_in[0], base, l);
    SRPArray decomp1 = gadget_decomp(rlwe_in[1], base, l);
    SRPArray decomp0_ft = sr_batch_ft(decomp0);
    SRPArray decomp1_ft = sr_batch_ft(decomp1);

    Polynomial r0 = Polynomial::zeros_ni(rlwe_in[0].ring_dimension());
    Polynomial r1 = Polynomial::zeros_ni(rlwe_in[0].ring_dimension());

    for (size_t level = 0; level < l; ++level) {
        r0 = sr_addp_ni(r0, sr_mulp_ni(decomp0_ft[level], gsw[4 * level]));
        r0 = sr_addp_ni(r0, sr_mulp_ni(decomp1_ft[level], gsw[4 * level + 1]));
        r1 = sr_addp_ni(r1, sr_mulp_ni(decomp0_ft[level], gsw[4 * level + 2]));
        r1 = sr_addp_ni(r1, sr_mulp_ni(decomp1_ft[level], gsw[4 * level + 3]));
    }

    r0 = sr_ift(r0);
    r1 = sr_ift(r1);

    SRPArray result(2);
    result[0] = r0;
    result[1] = r1;
    return result;
}

// ============================================================================
// CKKS Bootstrapping
// ============================================================================

MRPArray ckks_bootstrap(const MRPArray& ct_in, const MRPArray& /*aux_data*/) {
    // TODO: Full CKKS bootstrapping circuit
    return ct_in;
}

// ============================================================================
// FILE I/O — JSON
// ============================================================================

bool save_polynomial_json(const Polynomial& /*p*/, const std::filesystem::path& /*file*/) {
    // TODO: Implement JSON serialization
    return false;
}

bool load_polynomial_json(Polynomial& /*p*/, const std::filesystem::path& /*file*/) {
    return false;
}

bool save_mrp_json(const MRP& /*m*/, const std::filesystem::path& /*file*/) {
    return false;
}

bool load_mrp_json(MRP& /*m*/, const std::filesystem::path& /*file*/) {
    return false;
}

bool save_mrp_array_json(const MRPArray& /*arr*/, const std::filesystem::path& /*file*/) {
    return false;
}

bool load_mrp_array_json(MRPArray& /*arr*/, const std::filesystem::path& /*file*/) {
    return false;
}

}  // namespace niobium::fhetch
