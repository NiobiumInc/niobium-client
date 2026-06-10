#!/usr/bin/env python3
# Copyright 2024-present Niobium Microsystems, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
verify_result.py - correctness oracle for cosine similarity
"""
# Copyright (c) 2025, Amazon Web Services
# All rights reserved.
#
# This software is licensed under the terms of the Apache v2 License.
# See the LICENSE.md file for details.
import argparse
import sys
import numpy as np

# The payloads are vectors of 7 int16 numbers
PAYLOAD_DIM = 7

def main():
    """
    Usage:  python3 verify_result.py  <expected_file>  <result_file> [--count_only]
    Returns exit-code 0 if equal or if there are more than 32 expected results, 1 otherwise.
    Prints a message so the caller can log it.
    """
    # Parse arguments using argparse
    parser = argparse.ArgumentParser(description='Copmare expeocted vs obtained results.')
    parser.add_argument('expected_file', type=str,
                        help='File containing the expected results')
    parser.add_argument('result_file', type=str,
                        help='File containing the obtained results')
    parser.add_argument('--count_only', action='store_true',
                        help='Only # of matches, not payloads')

    args = parser.parse_args()

    if args.count_only: # not payloads
        # Read the expected and result binary files, containing just a counter
        expected_data = np.fromfile(args.expected_file, dtype=np.int_)
        result_data = np.fromfile(args.result_file, dtype=np.int_)

        if np.array_equal(expected_data, result_data):
            print(f"         [harness] PASS (result={expected_data})")
            sys.exit(0)
        else:
            print(f"         [harness] FAIL (expected {expected_data}",
                  f"but found {result_data})")
            sys.exit(1)

    # Read the expected and result binary files containing payload vectors
    expected_data = np.fromfile(args.expected_file, dtype=np.int16)
    result_data = np.fromfile(args.result_file, dtype=np.int16)

    # Reshape into payload vectors
    expected_payloads = expected_data.reshape(-1, PAYLOAD_DIM)
    result_payloads = result_data.reshape(-1, PAYLOAD_DIM)

    num_expected = len(expected_payloads)
    num_results = len(result_payloads)

    # This is the original code, but we want to skip the detailed comparison 
    # even if there are too many matches:
    #
    # If there are more than 32 expected results, always report success
    # if num_expected > 32:
    #     print(f"         [harness] PASS (Too many matches: {num_expected} > 32,",
    #           "skipping detailed comparison)")
    #     sys.exit(0)

    # Otherwise, compare the payloads
    if num_expected != num_results:
        print(f"         [harness] FAIL (Expected {num_expected} payloads, got {num_results})")
        sys.exit(1)

    # Compare each payload vector
    for i in range(num_expected):
        if not np.array_equal(expected_payloads[i], result_payloads[i]):
            print(f"         [harness] FAIL (Payload {i} mismatch)")
            print(f"  Expected: {expected_payloads[i]}")
            print(f"  Got:      {result_payloads[i]}")
            sys.exit(1)

    print(f"         [harness] PASS (All {num_expected} payload vectors match)")
    sys.exit(0)


if __name__ == "__main__":
    main()
