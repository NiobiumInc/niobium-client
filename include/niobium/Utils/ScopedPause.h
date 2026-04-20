#pragma once

// Compiler session API lives in libnbfhetch (consumed via the submodule).
// The compiler-repo version of this header used the un-prefixed form
// because its include dir pointed directly at include/niobium/; in this
// repo niobium-fhetch's public headers are reached through the niobium/
// prefix like the rest of the codebase.
#include "niobium/compiler.h"

namespace niobium {

/** 
* ScopedPause temporarily pauses the compiler if it is running. 
* When the ScopedPause object is destroyed, it resumes the compiler 
* if it was previously running.
*/
class ScopedPause {
  bool was_running_;

  public:
    ScopedPause() {
      auto& compiler = niobium::compiler();
      if (compiler.running_p()) {
        compiler.pause();
        was_running_ = true;
      } else {
        was_running_ = false;
      }
  }

  ~ScopedPause() {
    if (was_running_) {
      auto& compiler = niobium::compiler();
      compiler.resume();
    }
  }
};

} // namespace niobium
