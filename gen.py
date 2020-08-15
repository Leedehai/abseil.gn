#!/usr/bin/env python3
# Copyright (c) 2020 Leedehai. All rights reserved.
# Use of this source code is governed under the LICENSE.txt file.

# NOTE I could use asyncio, but disk-IO is not that slow in this
# use case.
"""
This project generates Abseil build files so that Abseil can serve as a
library embedded in another GN-managed project. Therefore, it does not transpile
the build configs such as compiler flags and whatnot.

Though this project was not solely applicable to Abseil, it was only tested
on Abseil.

Usage example ("--help" for help)
$ ./gen.py $PATH_TO_ABSEIL_CPP_REPO --profile absl.json
"""

import sys
# ast.get_source_segment is only availble in Python 3.8+.
assert sys.version_info.major == 3 and sys.version_info.minor >= 8

import argparse
import ast
import contextlib
import datetime
import glob
import io
import json
import os
import re
import subprocess
from collections import namedtuple
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, cast

GEN_YEAR = datetime.datetime.now().year
DEP_ROOT_SIGIL = re.compile(r"^@\S+")  # e.g. "@com_google_googletest"


def print_warn(s: str) -> None:
    sys.stderr.write("\x1b[33mwarning: %s\x1b[0m\n" % s)


def print_err(s: str) -> None:
    sys.stderr.write("\x1b[31merror: %s\x1b[0m\n" % s)


def bazel_cquery(label_pattern: str, build_options: List[str]) -> Optional[str]:
    """
    Return a string as if the targets were hand-written in the BUILD file.
    All variables and function calls (including glob(), select()) are expanded.
    """
    command = ["bazel", "cquery", label_pattern, "--output", "build"]
    if len(build_options) > 0:
        command += ["--define"] + build_options
    print(' '.join(command))
    try:
        stdout = subprocess.check_output(command, stderr=subprocess.DEVNULL)
        return stdout.decode()
    except subprocess.CalledProcessError:
        return None


class cd:
    def __init__(self, to_path: Path):
        self.to_path = to_path
        self.old_path = Path.cwd()

    def __enter__(self):
        os.chdir(self.to_path)

    def __exit__(self, etype, value, traceback):
        os.chdir(self.old_path)


BAZEL_BASENAMES = ["BUILD.bazel", "BUILD"]

# Keys in profile (optional)
PROFILE_TARGET_MAPPING = "target_mapping"
PROFILE_ROOT_REPLACERS = "root_replacers"
PROFILE_VARIABLES = "bazel_variable_expansions"
PROFILE_HIDDEN_TARGETS = "hidden_target_labels"
PROFILE_SKIP_TESTONLY = "skip_testonly"
PROFILE_BUILD_OPTIONS = "bazel_build_options"
PROFILE_GN_OMIT_IF_EMPTY = "gn_omit_if_empty"
PROFILE_REMOVE_GN_LIST_ELEMENTS = "remove_gn_list_elements"

GnAttribute = namedtuple("GnAttribute", ["name", "order"])

INTERESTED_BAZEL_ATTRS = {
    "name": None,
    "testonly": GnAttribute(name="testonly", order=5),
    "hdrs": GnAttribute(name="public", order=4),
    "srcs": GnAttribute(name="sources", order=3),
    "deps": GnAttribute(name="deps", order=2),
    "defines": GnAttribute(name="defines", order=1),
    "local_defines": GnAttribute(name="defines", order=1),
    "copts": GnAttribute(name="cflags", order=0),
    "linkopts": GnAttribute(name="ldflags", order=0),
    # NOTE No "visibility" here, as Bazel has a more complicated visibility
    # syntax than GN's "visibility" and it is hard to translate. In the
    # generated GN, the default visibility is ["*"] (no restriction), unless
    # the target name is matched by one of the glob pattern in profile's
    # "hidden_targets" list.
}
GN_ATTR_ORDER_LOOKUP = dict(
    (e.name, e.order) for e in INTERESTED_BAZEL_ATTRS.values() if e != None)


def gn_attr_sorted(iterable: Iterable[tuple]) -> List[tuple]:
    return sorted(iterable,
                  key=lambda tup: GN_ATTR_ORDER_LOOKUP.get(tup[0], 0),
                  reverse=True)  # Items with high order scores go first


def rectify_list_str(
        p: str,
        is_path: bool,  # is path string or label string
        source_dir_relative_to_repo: Optional[str],
        replacers: dict) -> str:
    p = p.replace("//" + source_dir_relative_to_repo + ":", ":")
    for k, v in replacers.items():
        if p.startswith(k):
            p = p.replace(k, v, 1)
            break
    if ":" in p:
        if is_path:
            p = p[1:] if p[0] == ':' else p.replace(':', '/')
        else:
            # "foo/bar/:baz" => "foo/bar:baz"
            p1, p2 = p.split(":", 1)
            if p != "/":
                return (p1[:-1] if p1.endswith("/") else p1) + ":" + p2
    return p


def prettify_path_str(p: Path) -> str:
    dot_pos = -1
    for i, e in enumerate(p.parts):
        if e in [".", ".."]:
            dot_pos = i
            break
    if dot_pos == -1:
        return str(p)
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
        return isinstance(node,
                          (ast.Constant, ast.Num, ast.Str, ast.NameConstant))
    except AttributeError:  # ast.Num, ast.Str, ast.NameConstant are unavailable.
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
    except AttributeError:  # ast.Num, ast.Str, ast.NameConstant are unavailable.
        raise NotImplementedError("unhandled constant node: %s" % type(node))


def eval_bazel_expr(code_text: Optional[str], profile: dict,
                    source_path: Path) -> Optional[Any]:
    if code_text == None or len(code_text) == 0:
        return None

    class _DisallowFunctions:
        @staticmethod
        def __getitem__(key):
            raise NameError

    def _bazel_select(choices: dict, **_) -> Any:
        # The logic doesn't exactly match what Bazel does (e.g. it doesn't
        # consider precedence if more than one choice keys are matched).
        for condition, value in sorted(choices.items(), key=lambda kv: kv[0]):
            if not condition.startswith("//"):
                if condition.startswith(":"):
                    condition = str(source_path.parent) + condition
                else:
                    condition = str(source_path.parent.joinpath(condition))
            if condition in profile.get(PROFILE_BUILD_OPTIONS, {}):
                return value
        # //conditions:default is a pseudo-label in Bazel. Like Bazel, if
        # no condition is picked up already, and //conditions:default is
        # absent from the provided choices, then an error is raised.
        return choices["//conditions:default"]

    def _bazel_glob(include: str,
                    exclude: Optional[List[str]] = None,
                    **_) -> List[str]:
        # The logic doesn't exactly match what Bazel does (e.g. it just
        # assumes exclude_directories is enabled).
        globbed = glob.glob(str(source_path.parent.joinpath(include)))
        to_exclude = [str(source_path.parent.joinpath(e)) for e in exclude]
        return [e for e in globbed if e not in to_exclude]

    with_globals = {
        "__builtins__": _DisallowFunctions,  # No Python builtin functions for security.
        "select": _bazel_select,
        "glob": _bazel_glob,
    }
    with_globals.update(profile.get(PROFILE_VARIABLES, {}))

    try:
        res = eval(code_text, with_globals)  # pylint: disable=eval-used
    except (NameError, KeyError, TypeError, SyntaxError):
        # KeyError can be caused by missing "//conditions:default" in select()
        # choices and none of the existing choices overlaps with profile's
        # PROFILE_SELECT_KEYS list.
        # TypeError can be caused by unrecognized variable names. To fix
        # it, define it in profile PROFILE_VARIABLES map.
        return None
    return res


def parse_bazel_build(
    source_text: str, profile: dict, source_path: Path,
    source_relative_to_repo: Optional[Path]
) -> Tuple[Optional[List[dict]], bool]:
    node = ast.parse(source_text)  # Bazel uses a subset of Python grammar.
    function_calls: List[ast.Call] = [
        n.value for n in node.body
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    ]
    decls: List[dict] = []  # Bazel declarations like cc_library(name=.., ..)
    has_printout = False
    for call in (c for c in function_calls if len(c.keywords) > 0):
        call_id = cast(ast.Name, call.func).id
        if call_id not in profile.get(PROFILE_TARGET_MAPPING, {}):
            continue
        kwargs: Dict[str, Any] = {}
        for kwarg_node in call.keywords:
            attr: Optional[str] = kwarg_node.arg
            if attr == None or attr not in INTERESTED_BAZEL_ATTRS:
                continue
            rhs = kwarg_node.value  # Node of the keyword arg assigned value.
            rhs_value = None  # Extracted value, i.e. not an AST node object.
            if is_constant_node(rhs):
                rhs_value = extract_constant_node(rhs)
            elif isinstance(rhs, ast.List):
                rhs_value = [extract_constant_node(e) for e in rhs.elts]
            elif isinstance(rhs, ast.Name):
                profile_vars = profile.get(PROFILE_VARIABLES, {})
                if rhs.id in profile_vars:
                    replace_with = profile_vars[rhs.id]
                    if replace_with != None:
                        rhs_value = profile_vars[rhs.id]
                else:
                    rhs_value = rhs.id
                    print_warn("L%-3d: not replacing %s = %s" %
                               (rhs.lineno, attr, rhs.id))
                    has_printout = True
            elif isinstance(
                    rhs, (ast.expr, ast.Call)):  # ast.expr is an abstract class
                print_err("aaaaaaaaaaaaaaaaaaaa")
                source_segment = ast.get_source_segment(source_text, rhs)
                eval_res = eval_bazel_expr(source_segment, profile, source_path)
                if eval_res == None:
                    print_err(  # If the expression gets complicated we can't do.
                        "L%-3d: '%s' right hand side unable to eval: %s" %
                        (rhs.lineno, attr, source_segment))
                    return None, True
                rhs_value = eval_res
            # Processing rhs_value and store it
            if rhs_value == None:
                continue
            if isinstance(rhs_value, list):
                rhs_value = [
                    rectify_list_str(e, attr in ["hdrs", "srcs"],
                                     str(source_relative_to_repo.parent),
                                     profile.get(PROFILE_ROOT_REPLACERS, {}))
                    for e in rhs_value
                ]
                for ee in (e for e in rhs_value if DEP_ROOT_SIGIL.match(e)):
                    print_warn("L%-3d: not replacing @.. in '%s': \"%s\"" %
                               (vars(rhs).get("lineno", "?"), attr, ee))
            kwargs[attr] = rhs_value
        assert "name" in kwargs
        item_dict = {"type": call_id, "name": kwargs["name"], "kwargs": kwargs}
        if ((kwargs.get("testonly", 0) == 0)
                or (profile.get(PROFILE_SKIP_TESTONLY, False)) == False):
            decls.append(item_dict)
    return decls, has_printout


def make_gn_build(source_relpath: Path, repo_info: Optional[str],
                  data_list: List[dict], profile: dict, verbose: bool) -> str:
    ss = io.StringIO()
    ss.write("# Copyright (c) %d Leedehai. All rights reserved.\n" % GEN_YEAR)
    ss.write("# Use of this source code is governed under the MIT license.\n")
    ss.write("# -----\n")
    ss.write("# Generated file. Do not modify manually.\n")
    if repo_info:
        ss.write("# upstream: %s\n" % repo_info)
    ss.write("# file: %s\n\n" % source_relpath)
    num_targets = len(data_list)
    if num_targets == 0:
        ss.write("# (empty)\n")
        return ss.getvalue()

    target_type_mapping = profile.get(PROFILE_TARGET_MAPPING, {})
    hidden_target_regexes = profile.get(PROFILE_HIDDEN_TARGETS, [])
    remove_gn_list_elements = profile.get(PROFILE_REMOVE_GN_LIST_ELEMENTS, {})
    proj_root_replacer = profile.get(PROFILE_ROOT_REPLACERS, {"//": "//"})["//"]
    for i, item_dict in enumerate(data_list):
        gn_target_type = target_type_mapping[item_dict["type"]]
        gn_target_name = item_dict["name"]
        gn_target_label = str(source_relpath.parent) + ":" + gn_target_name
        private_visibility_cause = None
        for regex_str in hidden_target_regexes:
            if re.search(regex_str, gn_target_label):
                private_visibility_cause = regex_str
                break
        gn_attrs = {}
        for in_attr, value in item_dict["kwargs"].items():
            if in_attr == "name" or value == None:
                continue
            gn_attrs[INTERESTED_BAZEL_ATTRS[in_attr].name] = value
        ss.write("# %s:%s\n" % (source_relpath.parent, gn_target_name))
        ss.write("%s(\"%s\") {\n" % (gn_target_type, gn_target_name))
        if private_visibility_cause:
            ss.write("  visibility = [\"%s:*\"]\n" % (proj_root_replacer))
        for k, v in gn_attr_sorted(gn_attrs.items()):
            if isinstance(v, list):
                if k in remove_gn_list_elements:
                    elements_to_remove = remove_gn_list_elements[k]
                    v = [e for e in v if e not in elements_to_remove]
                if (len(v) == 0
                        and k in profile.get(PROFILE_GN_OMIT_IF_EMPTY, [])):
                    continue
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


def gen_file(source_path: Path, repo_path: Optional[Path],
             repo_info: Optional[str], profile: dict,
             verbose: bool) -> Tuple[Optional[str], bool]:
    if repo_path:
        with cd(repo_path):
            source_text = bazel_cquery("%s:*" % source_path.parent,
                                       profile.get(PROFILE_BUILD_OPTIONS, []))
        if source_text == None:
            print_err("bazel can't query: %s" % source_path)
            return None, True
        source_relative_to_repo = Path(os.path.relpath(source_path, repo_path))
        data_list, has_printout = parse_bazel_build(source_text, profile,
                                                    source_path,
                                                    source_relative_to_repo)
    else:  # Input was single file
        with open(source_path, 'r') as f:
            data_list, has_printout = parse_bazel_build(f.read(), profile,
                                                        source_path, None)

    if data_list == None:
        return None, has_printout
    if repo_path:
        # NOTE Do not use Path.relative_to(), which cannot handle some cases
        # that can be handled by os.path.relpath():
        #   Path("a/b").relative_to(Path("c/d"))
        #     => ValueError: 'a/b' does not start with 'c/d'
        #   os.path.relpath("a/b", "c/d")
        #     => "../../a/b"
        source_relpath = Path(os.path.relpath(source_path, repo_path))
    else:
        source_relpath = Path(os.path.relpath(source_path))
    gn_content = make_gn_build(source_relpath, repo_info, data_list, profile,
                               verbose)
    return gn_content, has_printout


def work(args: argparse.Namespace) -> int:
    if args.profile:
        with open(args.profile, 'r') as f:
            profile = json.load(f)
        if not isinstance(profile, dict):
            print_err("profile should be a dict")
            return 1
    else:
        profile = {}

    input_path = Path(args.path)
    if input_path.is_file():
        gn_content, _ = gen_file(input_path, None, None, profile, args.verbose)
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
    parsing_error_file_num = 0
    total_builds = len(io_paths)
    sys.stderr.write("starting up..\n")
    for i, (bazel_path, gn_path) in enumerate(io_paths):
        status_str = "[%d/%d] GEN %s" % (i + 1, total_builds, bazel_path)
        if args.verbose:
            sys.stderr.write(status_str + "\n")
        else:
            sys.stderr.write("\x1b[1A\x1b[2K" + status_str + "\n")
        gn_content, has_printout = gen_file(bazel_path, input_path, repo_info,
                                            profile, args.verbose)
        if has_printout and not args.verbose:
            sys.stderr.write("\n")  # So text isn't elided by "\x1b[1A\x1b[2K"
        if gn_content == None:
            parsing_error_file_num += 1
        else:
            gn_contents[gn_path] = gn_content
    if parsing_error_file_num > 0:
        print_err("error in %s files, nothing written." %
                  parsing_error_file_num)
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
