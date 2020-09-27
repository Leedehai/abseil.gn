#!/usr/bin/env bash
# Copyright (c) 2020 Leedehai. All rights reserved.
# Use of this source code is governed under the LICENSE.txt file.

if [ "$1" == "-h" ] || [ "$1" == "--help" ]; then
  echo "Fetch and update Abseil GN build files"
  echo ""
  echo "Alterative behaviors:"
  echo "  -h, --help  show this help message and exit"
  echo "  -r, --reuse skip downloading from upstream"
  echo "  -p, --peek  check if a new Abseil revision is available"
  exit 0
fi

dirname=$(dirname $0)

abseil_temp=$dirname/abseil-temp
abseil_rev=$dirname/ABSL_REVISION.txt
abseil_url=https://github.com/abseil/abseil-cpp.git

set -e # Exit on error

if [ "$1" = "-p" ] || [ "$1" = "--peek" ]; then
  local_commit=$(tail -1 $abseil_rev)
  echo "local commit:  $local_commit"
  printf "remote commit: "
  remote_commit=$(git ls-remote $abseil_url HEAD | cut -f1)
  printf "\rremote commit: $remote_commit\n"
  if [ $local_commit = $remote_commit ]; then
    echo "no update"
  else
    echo "new revision available"
  fi
  exit 0
fi

if [ "$1" != "-r" ] && [ "$1" != "--reuse" ]; then
  rm -rf absl

  git clone --depth 1 $abseil_url $abseil_temp
  git --no-pager -C $abseil_temp log -1 --pretty=%cI > $abseil_rev
  git --no-pager -C $abseil_temp rev-parse HEAD >> $abseil_rev

  mv $abseil_temp/absl $dirname/absl # The meat of the library
  mv $abseil_temp/WORKSPACE $dirname/WORKSPACE # bazel needs it

  rm -rf $abseil_temp
fi

$dirname/gen.py . --profile=$dirname/absl.json # Help: use "--help"

echo "Mutated BUILD files:"
git status | grep BUILD || true
echo "Rolled to Abseil commit: "
cat $abseil_rev

commit_hash=$(tail -1 $abseil_rev | cut -c 1-7)
echo "Do: git add . && git commit -m \"[roll] Roll to Abseil $commit_hash\""
