# Abseil C++ Library, GN-managed

[The Abseil C++ Library](https://abseil.io) ([source](https://github.com/abseil/abseil-cpp))
is a collection of useful C++ utilities that are elusive in the C++ standard
library.

The original library supports using [Bazel](https://bazel.build) or
[CMake](https://cmake.org) as the build system. But I wanted to make it usable
with [GN](https://gn.googlesource.com/gn), which was developed for Chromium with
cross-platform and conditional builds in mind, and which I think is more
light-weight and syntactically elegant than Bazel
([in author's words](https://gn.googlesource.com/gn/+/refs/heads/master/docs/language.md#differences-and-similarities-to-blaze)).

This project generates Abseil build files so that Abseil can serve as a
library embedded in another GN-managed project. Therefore, it does not transpile
Bazel build settings stored in `WORKSPACE` and `*.bzl` files etc.

## Before using

:warning: The Abseil source code was not checked in, so you should update this
repository first (see below).

## Prerequisites

- Python 3.7+,
- Git 2.21+ and network connection (if updating the build).

## How to update this repository

Working directory: at root of this repository.

```bash
# Fetch Abseil source and update *.gn files
$ ./update.sh # add "-r" to skip fetching the Abseil source.
```

## Explore alternative actions

```bash
$ ./gen.py --help
```

## What is the profile file?

It controls how Bazel build files are converted to GN build files. The existing
[absl.json](absl.json) should be self-explanatory enough.

## License

[MIT License](LICENSE.txt).

■
