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
params.py - Parameters and directory structure for similarity search.
"""
# Copyright (c) 2025, Amazon Web Services
# All rights reserved.
#
# This software is licensed under the terms of the Apache v2 License.
# See the LICENSE.md file for details.
from pathlib import Path

# Enum for benchmark size
TOY = 0
SMALL = 1
MEDIUM = 2
LARGE = 3
TOY_LARGE_RING = 4
MEDIUM_1M = 5
TOY_2_BATCH = 6

def instance_name(size):
    """Return the string name of the instance size."""
    if size > TOY_2_BATCH:
        return "unknown"
    names = ["toy", "small", "medium", "large", "toy_large_ring", "medium_1M", "toy_2_batch"]
    return names[size]

# The payloads are vectors of 7 int16 numbers in the range [0,4095)
PAYLOAD_DIM = 7

class InstanceParams:
    """Parameters that differ for different instance sizes."""

    def __init__(self, size, rootdir=None):
        """Constructor."""
        self.size = size
        self.rootdir = Path(rootdir) if rootdir else Path.cwd()

        if size > TOY_2_BATCH:
            raise ValueError("Invalid instance size")

        # parameters for sizes:   toy  small   medium     large  toy_large_ring  medium_1M  toy_2_batch
        rec_dims =              [ 128,   128,     256,      512,            128,       256,         128]
        db_sizes =              [1000, 50000,  500000, 20000000,           1000,   1000000,        2000]

        self.record_dim = rec_dims[size]
        self.db_size = db_sizes[size]

    def get_size(self):
        """Return the instance size."""
        return self.size

    def get_record_dim(self):
        """Return the dimension of the plaintext record."""
        return self.record_dim

    def get_db_size(self):
        """Return the number of records in the dataset."""
        return self.db_size

    # Directory structure methods
    def subdir(self):
        """Return the submission directory of this repository."""
        return self.rootdir

    def datadir(self):
        """Return the dataset directory path."""
        return self.rootdir / "datasets" / instance_name(self.size)

    def iodir(self):
        """Return the I/O directory path."""
        return self.rootdir / "io" / instance_name(self.size)

    def measuredir(self):
        """Return the measurements directory path."""
        return self.rootdir / "measurements" / instance_name(self.size)
