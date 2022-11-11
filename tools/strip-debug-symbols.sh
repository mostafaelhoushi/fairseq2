#!/usr/bin/env bash

# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

set -eo pipefail

function print_usage
{
    echo "Usage: strip-debug-symbols [--extract] PATHNAME"
}

function exit_with_usage
{
    print_usage >&1

    exit 0
}

function exit_with_error
{
    print_usage >&2

    exit 1
}

if [[ $# -eq 0 || $# -gt 2 ]]; then
    exit_with_error
fi

if [[ $# -eq 1 ]]; then
    if [[ $1 == -h || $1 == --help ]]; then
        exit_with_usage
    fi
else
    if [[ $1 != --extract ]]; then
        exit_with_error
    fi

    should_extract=true

    shift
fi

target=$1

if [[ $(uname -s) == Darwin ]]; then
    if [[ $should_extract == true ]]; then
        # Extract the debug symbols.
        dsymutil --minimize -o "$target.dSYM" "$target"
    fi

    strip -r -x "$target"
else
    if [[ $should_extract == true ]]; then
        # Extract the debug symbols.
        objcopy --only-keep-debug "$target" "$target.debug"

        # Associate the debug file with the target.
        objcopy --add-gnu-debuglink="$target.debug" "$target"
    fi

    objcopy --strip-unneeded "$target"
fi