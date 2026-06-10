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
generate_query.py - Generate a random query for fetch-by-similarity.
"""
# Copyright (c) 2025, Amazon Web Services
# All rights reserved.
#
# This software is licensed under the terms of the Apache v2 License.
# See the LICENSE.md file for details.
import random
import argparse
import numpy as np
from params import InstanceParams, TOY, LARGE, TOY_LARGE_RING, MEDIUM_1M, TOY_2_BATCH

def main():
    """
    Generate a random query vector and write to disk
    """
    # Parse arguments using argparse
    parser = argparse.ArgumentParser(description='Generate query for FHE benchmark.')
    parser.add_argument('size', type=int, choices=range(TOY, TOY_2_BATCH+1),
                        help='Dataset size (0-toy/1-small/2-medium/3-large/4-toy_large_ring)')
    parser.add_argument('--seed', type=int, help='Random seed for reproducibility')
    
    args = parser.parse_args()
    size = args.size
    
    # Set random seed if provided
    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)

    # Use params.py to get instance parameters
    params = InstanceParams(size)
    dim = params.get_record_dim()

    # Get dataset directory from params
    dataset_dir = params.datadir()

    # Generate a new query: First choose a random vector on the unit sphere in
    # dimension dim, made out of float32
    rng = np.random.default_rng()
    qry = rng.standard_normal(dim, dtype=np.float32)

    # With probability 50%, keep the query as the new vector qry.
    # Otherwise, read a random center from the centers file and set the
    # query as center + 0.3*qry.
    if random.randint(0, 1) == 0:
        centers_file = dataset_dir / "centers.bin"
        centers = np.fromfile(centers_file, dtype=np.float32).reshape(-1,dim)
        center = centers[random.randint(0, len(centers)-1)]
        qry = center + (0.3 * qry / np.linalg.norm(qry))

    qry /= np.linalg.norm(qry) # normalize to unit length

    # store the query to file
    query_file = dataset_dir / "query.bin"
    qry.tofile(query_file)


if __name__ == "__main__":
    main()
