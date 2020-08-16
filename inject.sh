#!/usr/bin/env bash
# Copyright (c) 2020 Leedehai. All rights reserved.
# Use of this source code is governed under the LICENSE.txt file.

if [ "$1" == "-h" ] || [ "$1" == "--help" ]; then
  echo "Copy Abseil GN build files to an Abseil source checkout"
  echo "  path        path of the abseil-cpp repository"
  echo "  -h, --help  show this help message and exit"
  exit 0
fi

dirname=$(dirname $0)
abseil_checkout=$1

if [ ! -d $abseil_checkout ]; then
  echo "Not a directory: $abseil_checkout"
  exit 1
fi

if [ ! -d $abseil_checkout/.git ]; then
  echo "Not a Git repository: $abseil_checkout/.git"
  exit 1
fi

set -e # Exit on error

# There is no portable pure-Bash script to compute relative paths.
# Credit: https://stackoverflow.com/a/31236568
# echo $(relpath somepath)
# echo $(relpath somepath /etc)  # relative to /etc
function relpath() {
  python -c "import os,sys;print(os.path.relpath(*(sys.argv[1:])))" "$@";
}

absl_commit=$(tail -1 $dirname/ABSL_REVISION.txt)

echo "$abseil_checkout:"
git -C $abseil_checkout -c advice.detachedHead=false checkout $absl_commit

for existing_gn in `find $dirname/absl -name "*.gn"`
do
  dest_dir=$abseil_checkout/$(relpath $(dirname $existing_gn) $dirname)
  echo "cp $existing_gn $dest_dir"
  cp $existing_gn $dest_dir
done
