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
cleartext_impl.py - Cleartext reference implementation for fetch-by-similarity

This module provides a baseline implementation for the FHE fetch-by-similarity
benchmark that operates on cleartext data.
"""
# Copyright (c) 2025, Amazon Web Services
# All rights reserved.
#
# This software is licensed under the terms of the Apache v2 License.
# See the LICENSE.md file for details.
import argparse
import numpy as np
from params import InstanceParams, TOY, LARGE, TOY_LARGE_RING, MEDIUM_1M, TOY_2_BATCH

# The payloads are vectors of 7 int16 numbers in the range [0,4095)
PAYLOAD_DIM = 7

def main():
    """
    Usage: python3 cleartext_impl.py <size> (0-toy/1-small/2-medium/3-large)
    * Reads dataset matrix M, payload vectors, and a query vector v
    * Compute the vector of similarities sim = M*v
    * Extract that payload vectors for rows i for which sim[i]>0.8
    * Sort the extracted vectors and write to disk
    """
    # Parse arguments using argparse
    parser = argparse.ArgumentParser(description='Cleartext implementation of fetch-by-similarity workload.')
    parser.add_argument('size', type=int, choices=range(TOY, TOY_2_BATCH+1),
                        help='Instance size (0-toy/1-small/2-medium/3-large/4-toy_large_ring)')
    parser.add_argument('--count_only', action='store_true',
                        help='Only count # of matches, do not return payloads')

    args = parser.parse_args()
    size = args.size

    # Use params.py to get instance parameters
    params = InstanceParams(size)
    dim = params.get_record_dim()

    # Get dataset directory from params
    dataset_dir = params.datadir()
    db = np.fromfile(dataset_dir / "db.bin", dtype=np.float32).reshape(-1, dim)

    # read the query from file, a single record of dimension dim
    v = np.fromfile(dataset_dir / "query.bin", dtype=np.float32)

    # Compute the similarities between the query and all the vectors in db
    sim = db @ v # matrix multiplication
    matches = sim > 0.8

    if args.count_only:
        # Write to file the number of matches, as an int16
        n_matches: np.int_ = matches.sum()
        n_matches.tofile(dataset_dir/"expected.bin")
        # NOTE: to_file write complete machine words, even if the value is short

    else:
        # Read the payloads, vectors of dimension PAYLOAD_DIM=7
        payload_file = dataset_dir / "payloads.bin"
        payloads = np.fromfile(payload_file, dtype=np.int16).reshape(-1, PAYLOAD_DIM)
        # Extract the payload vectors for the matches
        extracted_payloads = payloads[matches]

        # Sort the payload vectors lexicographically and write to disk
        sorted_ps = extracted_payloads[np.lexsort(extracted_payloads.T[::-1])]
        sorted_ps.tofile(dataset_dir/"expected.bin")


if __name__ == "__main__":
    main()
