#!/usr/bin/env python3
# Copyright (c) 2020 Leedehai. All rights reserved.
# Use of this source code is governed under the LICENSE.txt file.

# NOTE I could use asyncio, but disk-IO is not that slow in this
# use case.
"""
Converts the [Abseil C++ library](https://abseil.io/docs/cpp/)'s
[Bazel](https://bazel.build/) build files to [GN](https://gn.googlesource.com/gn)
build files.

This project generates Abseil build files so that Abseil can serve as a
library embedded in another GN-managed project. Therefore, it does not transpile
the build configs such as compiler flags and whatnot.

Though this project was not solely applicable to Abseil, it was only tested
on Abseil.

Usage example ("--help" for help)
$ ./gen.py $PATH_TO_ABSEIL_CPP_REPO --profile absl.json
"""

import sys
assert sys.version_info.major == 3 and sys.version_info.minor >= 7

import argparse
import ast
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

GEN_YEAR = datetime.datetime.now().year


def print_warn(s: str) -> None:
    sys.stderr.write("\x1b[33mwarning: %s\x1b[0m\n" % s)


def print_err(s: str) -> None:
    sys.stderr.write("\x1b[31merror: %s\x1b[0m\n" % s)


BAZEL_BASENAMES = [
    "BUILD.bazel",
    "BUILD",
]

# Keys in profile (optional)
PROFILE_TARGET_MAPPING = "target_mapping"
PROFILE_ROOT_REPLACERS = "root_replacers"
PROFILE_VARIABLES = "variable_expansions"

INTERESTED_ATTRS = {
    "name": None,
    "testonly": "testonly",
    "hdrs": "public",
    "srcs": "sources",
    "deps": "deps",
    "copts": "cflags",
    "linkopts": "ldflags",
}


def rectify_label_str(p: str, replacers: dict) -> str:
    for k, v in replacers.items():
        if p.startswith(k):
            p = p.replace(k, v, 1)
            break
    return p.replace("/:", ":", 1)


def prettify_path_str(p: Path) -> str:
    dot_pos = -1
    for i, e in enumerate(p.parts):
        if e in [".", ".."]:
            dot_pos = i
            break
    if dot_pos == -1:
        return str(p)
    else:
        return os.path.join(*p.parts[dot_pos + 1:])


def stringify_list(arr: List[str],
                   initial_indent: int = 2,
                   item_indent: int = 2) -> str:
    assert isinstance(arr, list)
    if len(arr) == 0:
        return "[]"
    assert isinstance(arr[0], str)
    if len(arr) == 1:
        return "[\"%s\"]" % arr[0]
    arr_dollar = [e for e in arr if e.startswith("$")]
    ss = io.StringIO()
    ss.write("[\n")
    for e in [ee for ee in arr if ee not in arr_dollar] + arr_dollar:
        ss.write("%s\"%s\",\n" % (" " * (initial_indent + item_indent), e))
    ss.write("%s]" % (" " * initial_indent))
    return ss.getvalue()


def maybe_get_repo_info(dir_path: Path) -> Optional[str]:
    info_txt = dir_path.joinpath("ABSL_REVISION.txt")
    if not info_txt.is_file():
        return None
    with open(info_txt, 'r') as f:
        return f.read().strip().replace("\n", " ")

def is_constant_node(node) -> bool:
    # ast.Num, ast.Str, ast.NameConstant are deprecated since Python 3.8 (though
    # still available for a while), and all constants are classified as
    # ast.Constant since then.
    try:
        return isinstance(node, (ast.Constant, ast.Num, ast.Str, ast.NameConstant))
    except AttributeError: # ast.Num, ast.Str, ast.NameConstant are unavailable.
        return isinstance(node, ast.Constant)

def extract_constant_node(node) -> Any:
    # ast.Num, ast.Str, ast.NameConstant are deprecated since Python 3.8 (though
    # still available for a while), and all constants are classified as
    # ast.Constant since then.
    if isinstance(node, ast.Constant):
        return cast(ast.Constant, node).value
    try:
        if isinstance(node, ast.Num):
            return cast(ast.Num, node).n
        if isinstance(node, ast.Str):
            return cast(ast.Str, node).s
        if isinstance(node, ast.NameConstant):
            return cast(ast.NameConstant, node).value
    except AttributeError: # ast.Num, ast.Str, ast.NameConstant are unavailable.
        raise NotImplementedError("unhandled constant node: %s" % type(node))


def read_bazel_build(source: io.TextIOWrapper,
                     profile: dict) -> Tuple[Optional[List[dict]], bool]:
    node = ast.parse(source.read())
    function_calls: List[ast.Call] = [
        n.value for n in node.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    calls: List[dict] = []
    target_names = set()
    has_printout = False
    for call in (c for c in function_calls if len(c.keywords) > 0):
        call_id = cast(ast.Name, call.func).id
        call_lineno = call.lineno
        if call_id not in profile.get(PROFILE_TARGET_MAPPING, {}):
            continue
        kwargs = {}
        for kwarg in call.keywords:
            attr: str = kwarg.arg
            if attr not in INTERESTED_ATTRS:
                continue
            value = kwarg.value
            if is_constant_node(value):
                kwargs[attr] = extract_constant_node(value)
            elif isinstance(value, ast.List):
                kwargs[attr] = [
                    rectify_label_str(
                        extract_constant_node(e),
                        profile.get(PROFILE_ROOT_REPLACERS, {}))
                    for e in value.elts
                ]
            elif isinstance(value, ast.Name):
                profile_vars = profile.get(PROFILE_VARIABLES, {}).get(attr, {})
                if value.id in profile_vars:
                    replace_with = profile_vars[value.id]
                    if replace_with != None:
                        kwargs[attr] = profile_vars[value.id]
                else:
                    kwargs[attr] = value.id
                    print_warn("L%-3d: not replacing %s = %s" %
                               (value.lineno, attr, value.id))
                    has_printout = True
        assert "name" in kwargs
        target_names.add(kwargs["name"])
        item_dict = {"type": call_id, "lineno": call_lineno}
        item_dict.update(kwargs)
        calls.append(item_dict)
    assert len(calls) == len(target_names)
    return calls, has_printout


def make_gn_build(source_path: Path, repo_info: Optional[str],
                  data_list: List[dict], target_type_mapping: dict,
                  verbose: bool) -> str:
    ss = io.StringIO()
    source_path_prettified = prettify_path_str(source_path)
    ss.write("# Copyright (c) %d Leedehai. All rights reserved.\n" % GEN_YEAR)
    ss.write("# Use of this source code is governed under the MIT license.\n")
    ss.write("# Generated file. Do not modify manually.\n")
    if repo_info:
        ss.write("# upstream: %s\n" % repo_info)
    ss.write("# file: %s\n\n" % source_path_prettified)
    num_targets = len(data_list)
    if num_targets == 0:
        ss.write("# (empty)\n")
        return ss.getvalue()

    for i, item_dict in enumerate(data_list):
        gn_target_type = target_type_mapping[item_dict["type"]]
        gn_target_name = item_dict["name"]
        gn_attrs = {}
        for in_attr, value in item_dict.items():
            if in_attr in ["type", "name", "lineno"]:
                continue
            if value == None:
                continue
            gn_attrs[INTERESTED_ATTRS[in_attr]] = value
        ss.write("# %s:%d\n" % (source_path_prettified, item_dict["lineno"]))
        ss.write("%s(\"%s\") {\n" % (gn_target_type, gn_target_name))
        for k, v in gn_attrs.items():
            if isinstance(v, list):
                ss.write("  %s = %s\n" % (k, stringify_list(sorted(v))))
            elif k == "testonly":
                ss.write("  %s = %s\n" % (k, "true" if v else "false"))
            else:
                ss.write("  %s = %s\n" % (k, v))
        ss.write("}\n")
        if i != num_targets - 1:
            ss.write("\n")
    gn_content = ss.getvalue()
    if verbose:
        print(gn_content)
    return gn_content


def gen_file(source_path: Path, repo_info: Optional[str], profile: dict,
             verbose: bool) -> Tuple[Optional[str], bool]:
    with open(source_path, 'r') as f:
        data_list, has_printout = read_bazel_build(f, profile)
    if data_list == None:
        return None, has_printout
    target_type_mapping = profile.get(PROFILE_TARGET_MAPPING, {})
    return make_gn_build(source_path, repo_info, data_list, target_type_mapping,
                         verbose), has_printout


def work(args: argparse.Namespace) -> int:
    if args.profile:
        with open(args.profile, 'r') as f:
            profile = json.load(f)
    else:
        profile = {}

    input_path = Path(args.path)
    if input_path.is_file():
        gn_content, _ = gen_file(input_path, None, profile, args.verbose)
        if gn_content != None:
            print(gn_content)
            return 0
        return 1

    repo_info: Optional[str] = maybe_get_repo_info(input_path)

    io_paths: List[Tuple[Path, Path]] = []
    for dirpath, _, filenames in os.walk(input_path):
        gn_prefix = Path(args.prefix) if args.prefix else input_path
        gn_dirpath = gn_prefix.joinpath(os.path.relpath(dirpath, input_path))
        io_paths += [(Path(dirpath).joinpath(e),
                      gn_dirpath.joinpath(e).with_suffix(".gn"))
                     for e in filenames if e in BAZEL_BASENAMES]

    if args.clean:
        for _, gn_path in io_paths:
            print("c: %s" % gn_path)
            with contextlib.suppress(FileNotFoundError):
                os.remove(gn_path)
            if gn_path.parent.exists() and os.listdir(gn_path.parent) == 0:
                os.removedirs(gn_path.parent)  # Recursively remove empty dirs.
        return 0

    gn_contents: Dict[Path, str] = {}
    total_builds = len(io_paths)
    sys.stderr.write("starting up..\n")
    for i, (bazel_path, gn_path) in enumerate(io_paths):
        status_str = "[%d/%d] GEN %s" % (i + 1, total_builds, bazel_path)
        if args.verbose:
            sys.stderr.write(status_str + "\n")
        else:
            sys.stderr.write("\x1b[1A\x1b[2K" + status_str + "\n")
        gn_content, has_printout = gen_file(bazel_path, repo_info, profile,
                                            args.verbose)
        if has_printout and not args.verbose:
            sys.stderr.write("\n")  # Avoid being elided by "\x1b[1A\x1b[2K"
        if gn_content != None:
            gn_contents[gn_path] = gn_content
        else:
            print_err("error in %s, nothing written." % bazel_path)
            return 1

    written = 0
    if not args.dry_run:
        for gn_path, gn_content in gn_contents.items():
            os.makedirs(gn_path.parent, exist_ok=True)
            with open(gn_path, 'w') as f:
                f.write(gn_content)
            written += 1

    print("%d files scanned, %d files generated." % (total_builds, written))
    return 0


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Bazel to GN converter.",
        epilog="If the input is an individual file, then print to stdout")
    arg_parser.add_argument("path",
                            type=str,
                            help="source project root or an individual file")
    arg_parser.add_argument("--profile",
                            type=str,
                            default=None,
                            help="path to the config settings (default: none)")
    arg_parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        help="root of generated files (default: in source tree)")
    arg_parser.add_argument("-c",
                            "--clean",
                            action="store_true",
                            help="clean generated files")
    arg_parser.add_argument("-d",
                            "--dry-run",
                            action="store_true",
                            help="dry-run: not writing")
    arg_parser.add_argument("-v",
                            "--verbose",
                            action="store_true",
                            help="verbose")
    args = arg_parser.parse_args()

    if not os.path.exists(args.path):
        print_err("path not found: %s" % args.path)
        return 1
    if args.profile and not os.path.exists(args.profile):
        print_err("profile not found: %s" % args.profile)

    return work(args)


if __name__ == "__main__":
    sys.exit(main())
