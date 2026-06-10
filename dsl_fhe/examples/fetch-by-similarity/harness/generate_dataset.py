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
generate_dataset.py - Generate random centers, database points, and payloads
for the fetch-by-similarity benchmark.
"""
# Copyright (c) 2025, Amazon Web Services
# All rights reserved.
#
# This software is licensed under the terms of the Apache v2 License.
# See the LICENSE.md file for details.
import random
import argparse
import numpy as np
from params import InstanceParams, TOY, LARGE, TOY_LARGE_RING, MEDIUM_1M, TOY_2_BATCH, PAYLOAD_DIM, instance_name

def generate_db_points(n_records: int, n_centers: int, dim: int) -> tuple:
    """
    Generate database points, half as random points and the other half by
    selecting random centers and adding noise.
    
    Args:
        n_records: Number of database records to generate
        n_centers: Number of centers to use
        dim: Dimension of the space
        
    Returns:
        Tuple containing:
        - Array of shape (n_records, dim) containing the database points
        - Array of shape (n_centers, dim) containing the centers
    """
    rng = np.random.default_rng()

    # Generate centers on the unit sphere
    centers = rng.standard_normal(size=(n_centers, dim), dtype=np.float32)
    for i in range(n_centers):
        centers[i] /= np.linalg.norm(centers[i])

    # Generate database points. Each one is either a random point on the unit
    # sphere (with probability 50%), or obtained by selecting a random center
    # and adding noise.
    db = rng.standard_normal((n_records, dim), dtype=np.float32)
    for i in range(n_records):
        if random.randint(0, 1) == 0:
            center = centers[random.randint(0, len(centers)-1)]
            db[i] = center + (0.3 * db[i] / np.linalg.norm(db[i]))
        db[i] /= np.linalg.norm(db[i])  # normalize to unit length

    return db, centers

def generate_payloads(n_records: int) -> np.ndarray:
    """
    Generate random payload vectors with int16 values in range [0, 4095).
    
    Args:
        n_records: Number of payload records to generate
        
    Returns:
        Array of shape (n_records, PAYLOAD_DIM=7) with the payload vectors
    """
    rng = np.random.default_rng()
    return rng.integers(low=0, high=4096,
                        size=(n_records, PAYLOAD_DIM), dtype=np.int16)


def main():
    """
    Generate random centers, database points, and payloads for the fetch-by-similarity benchmark.
    """
    # Parse arguments using argparse
    parser = argparse.ArgumentParser(description='Generate dataset for FHE benchmark.')
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
    n_records = params.get_db_size()

    # Calculate number of centers
    n_centers = max(1, int(n_records / 32))

    # Get dataset directory from params and ensure it exists
    dataset_dir = params.datadir()
    dataset_dir.mkdir(parents=True, exist_ok=True)

    # Generate database points and centers, and then payloads
    db, centers = generate_db_points(
        n_records, n_centers, params.get_record_dim())
    payloads = generate_payloads(n_records)

    # Write data to files
    db.tofile(dataset_dir / "db.bin")
    centers.tofile(dataset_dir / "centers.bin")
    payloads.tofile(dataset_dir / "payloads.bin")


if __name__ == "__main__":
    main()
