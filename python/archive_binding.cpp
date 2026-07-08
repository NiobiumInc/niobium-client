// pybind11 binding for the FHETCH TLV archive (src/fhetch_transport/archive.cpp).
//
// Exposes pack_directory / unpack_into so the pure-Python submit() client can
// pack a project dir and unpack the returned probes WITHOUT reimplementing the
// "NBAR" wire format in Python — the C++ archive.cpp stays the single source of
// truth (a change there flows in via rebuild). Pure C++ stdlib; no OpenFHE.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <filesystem>
#include <set>
#include <string>
#include <vector>

#include "archive.h"

namespace py = pybind11;
namespace nft = niobium::fhetch_transport;

PYBIND11_MODULE(_archive, m) {
    m.doc() = "FHETCH TLV (\"NBAR\") archive pack/unpack";

    // Pack every regular file under `root` except those whose top-level path
    // component is in `exclude_top` (default: serialized_probes/ — the response
    // payload, per client.cpp). Returns the archive as bytes.
    m.def("pack_directory",
          [](const std::string &root, std::vector<std::string> exclude_top) {
              std::set<std::string> ex(exclude_top.begin(), exclude_top.end());
              std::string buf = nft::pack_directory(
                  root, [&ex](const std::filesystem::path &rel) {
                      return rel.empty() ||
                             ex.find((*rel.begin()).string()) == ex.end();
                  });
              return py::bytes(buf);
          },
          py::arg("root"),
          py::arg("exclude_top") = std::vector<std::string>{"serialized_probes"});

    // Unpack an archive (bytes) into `dest`, returning the file count. pybind's
    // std::string caster accepts bytes as a raw byte buffer.
    m.def("unpack_into",
          [](const std::string &archive, const std::string &dest) {
              return nft::unpack_into(archive, dest);
          },
          py::arg("archive"), py::arg("dest"));
}
