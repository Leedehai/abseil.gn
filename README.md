# Abseil C++ Library, GN-managed

[The Abseil C++ Library](https://abseil.io) ([source](https://github.com/abseil/abseil-cpp))
is a collection of useful C++ utilities that are elusive in the C++ standard
library. [Why adopt Abseil?](https://abseil.io/about/philosophy)

The official repository supports using [Bazel](https://bazel.build) or
[CMake](https://cmake.org) as the build system. But I want to make it usable
with [GN](https://gn.googlesource.com/gn), a build system developed by and for
the Chromium projects with cross-platform and conditional builds in mind, and
which I think is more light-weight and syntactically elegant than Bazel
([GN vs Bazel](https://gn.googlesource.com/gn/+/refs/heads/master/docs/language.md#differences-and-similarities-to-blaze)).

This project generates GN build files so that Abseil can serve as a library
embedded in another GN-managed project. Therefore, it does not transpile Bazel
build settings stored in `WORKSPACE` and `*.bzl` files etc.

## Before using

:warning: The Abseil source code was not checked in, so you should update this
repository first (see below).

## Prerequisites

- Python 3.8+,
- Git 2.21+ and network connection (if updating the build).
- Bazel 3.4.1+ (we need it to extract target definitions)

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

It controls how Bazel build files are converted to GN build files. Boradly
speaking, the JSON file serves two purposes:
- Controls the generation of GN files.
- Circumvents complex configuration computation in Bazel.

The profile could be auto-generated by a GN target so its content can depend
on how GN handles conditionals.

### `target_mapping`

Type: `dict[str, str]` (key: apparent Bazel target type, value: apparent GN
target type). Default: empty.

Controls what Bazel target types get transpiled into what GN target types (for
example, `cc_library` can correspond to `source_set`, `shared_library`,
`static_library`, or even a custom GN target template name like `absl_source_set`).
Bazel targets whose type doesn't appear in this map are ignored.

### `hidden_target_labels`

Type: `list[str]` (elements: regular expressions). Default: empty.

If a target's full label (i.e. `path/to/build/file:target_name`) is matched with
any of the regular expressions, the target's visibility is private to the Bazel
project itself. Otherwise, the target is publicly visible.

Indeed, Bazel targets have their own visibility annotations the grammar is too
complicated (e.g. special names like `__pkg__`, `__subpackages__`, or a package
group), and isn't particularly useful to retain this fine-grained control in the use case where the Bazel project serves as a library - what really matters is whether clients can refer to a target or not.

### `skip_testonly`

Type: `bool`. Default: `false`.

Whether to skip Bazel targets which have `testonly = 1`.

### `root_replacers`

Type: `dict[str, str]` (key: root path in Bazel syntax, value: paths in GN
syntax). Default: empty.

Bazel can handle foreign packages but GN was designed with mono-repo in mind.
Therefore, users may need to replace package root path symbols with symbols
understandable by GN. For example, `@com_google_googletest//` can be replaced
with `$googletest/`, and the user need to specify in their GN setup what
path string the variable `googletest` is mapped to.

### `bazel_select_keys`

Type: `list[str]`. Default: empty.

Bazel has a special function `select()`, whose input is a mapping from `config_setting` labels to possible outputs, and the output depends on which
`config_setting` fires. However, the rule to determine which `config_setting`
fires is complex, so `bazel_select_keys` provides a way for user to directly
specify which should fire.

Note the element strings are full label of the
original `config_setting`, e.g. `path/to/build/file:config_setting_foo`, not
something like `:config_setting_foo`, though the latter is what may appear in
a Bazel file's `select()` invocations.

### `bazel_variable_expansions`

Type: `dict[str, Any]`. Default: empty.

Controls what values Bazel variables should represent. In Bazel files, their
values are determined by the context - the assignment statement may appear
elsewhere, even in other files via a `load` statement. This is complex.
`bazel_variable_expansions` provides a way for user to directly specify their
values.

### `gn_omit_if_empty`

Type: `list[str]`. Default: empty.

Controls what GN attributes should be left out if their values are empty lists.
It serves a cosmetic purpose, in that lots of empty attributes like `cflags = []`
is ugly.

### `remove_gn_list_elements`

Type `Dict[str, List[str]]`. Default: empty.

Remove list elements in the corresponding any target's GN attribute.

## License

[MIT License](LICENSE.txt).

■
