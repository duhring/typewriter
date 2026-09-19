"""Tests for the bootstrap Python and toolchain check (tools/env_check.py).

Every environmental fact is injected, so these run the same on any machine.
Run from the repo root:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))

import env_check  # noqa: E402

ARM64 = 0x0100000C
X86_64 = 0x01000007


def fat_binary(*cputypes: int) -> bytes:
    head = struct.pack(">II", 0xCAFEBABE, len(cputypes))
    for cputype in cputypes:
        head += struct.pack(">IIIII", cputype, 0, 0, 0, 0)
    return head + b"\0" * 64


def thin_binary(cputype: int) -> bytes:
    return struct.pack("<II", 0xFEEDFACF, cputype) + b"\0" * 64


def codes(report: dict) -> list[str]:
    return [entry["code"] for entry in report["problems"]]


class TestMachOParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_universal_binary_lists_every_arch(self):
        self.assertEqual(env_check.macho_archs(self.write("clang", fat_binary(X86_64, ARM64))), ["x86_64", "arm64"])

    def test_thin_arm64_binary(self):
        self.assertEqual(env_check.macho_archs(self.write("clang", thin_binary(ARM64))), ["arm64"])

    def test_thin_x86_64_binary(self):
        self.assertEqual(env_check.macho_archs(self.write("clang", thin_binary(X86_64))), ["x86_64"])

    def test_unreadable_or_foreign_files_are_empty(self):
        self.assertEqual(env_check.macho_archs(self.write("script", b"#!/bin/sh\necho hi\n")), [])
        self.assertEqual(env_check.macho_archs(self.dir / "missing"), [])
        self.assertEqual(env_check.macho_archs(self.write("short", b"\xca\xfe")), [])

    @unittest.skipUnless(sys.platform == "darwin", "reads the real developer tools")
    def test_real_toolchain_contains_the_host_arch(self):
        clang = env_check.toolchain_clang()
        if clang is None:
            self.skipTest("no clang installed")
        self.assertIn(env_check.host_arch(), env_check.macho_archs(clang))


class TestDecision(unittest.TestCase):
    def check(self, **overrides):
        kwargs = dict(
            interpreter="/opt/homebrew/bin/python3.13",
            version=(3, 13, 7),
            machine="arm64",
            host="arm64",
            clang=Path("/Library/Developer/CommandLineTools/usr/bin/clang"),
            clang_archs=["arm64"],
        )
        kwargs.update(overrides)
        return env_check.check(**kwargs)

    def test_native_python_on_native_toolchain_passes(self):
        report = self.check()
        self.assertTrue(report["ok"], report)
        self.assertFalse(report["rosetta"])
        self.assertEqual(report["problems"], [])

    def test_intel_python_on_apple_silicon_with_arm_only_tools_fails_twice(self):
        report = self.check(interpreter="/usr/local/bin/python3.13", machine="x86_64")
        self.assertFalse(report["ok"])
        self.assertEqual(codes(report), ["rosetta", "toolchain_arch"])
        self.assertIn("cryptography", report["problems"][1]["message"])
        self.assertIn("/opt/homebrew/bin/python3", report["problems"][0]["fix"])

    def test_intel_python_with_universal_tools_is_rosetta_only(self):
        report = self.check(machine="x86_64", clang_archs=["x86_64", "arm64"])
        self.assertEqual(codes(report), ["rosetta"])

    def test_allow_rosetta_downgrades_to_a_warning_when_tools_can_build(self):
        report = self.check(machine="x86_64", clang_archs=["x86_64", "arm64"], allow_rosetta=True)
        self.assertTrue(report["ok"])
        self.assertEqual([w["code"] for w in report["warnings"]], ["rosetta"])

    def test_allow_rosetta_cannot_override_an_impossible_toolchain(self):
        report = self.check(machine="x86_64", clang_archs=["arm64"], allow_rosetta=True)
        self.assertFalse(report["ok"])
        self.assertEqual(codes(report), ["toolchain_arch"])

    def test_old_python_fails(self):
        report = self.check(version=(3, 10, 12))
        self.assertEqual(codes(report), ["version_too_old"])

    def test_intel_mac_with_intel_python_passes(self):
        report = self.check(interpreter="/usr/local/bin/python3.13", machine="x86_64", host="x86_64", clang_archs=["x86_64", "arm64"])
        self.assertTrue(report["ok"], report)

    def test_no_toolchain_is_reported_but_not_fatal(self):
        report = self.check(clang=None, clang_archs=[])
        self.assertTrue(report["ok"])
        self.assertIsNone(report["toolchain"]["clang"])
        self.assertIn("xcode-select --install", env_check.render(report))


class TestVenvValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.venv = self.dir / "venv"
        self.venv.mkdir()

    def make_base(self) -> Path:
        base = self.dir / "base" / "bin"
        base.mkdir(parents=True)
        (base / "python3").write_text("", encoding="utf-8")
        return base

    def cfg(self, home: Path | str, version: str = "3.13.7"):
        (self.venv / "pyvenv.cfg").write_text(f"home = {home}\nversion = {version}\n", encoding="utf-8")

    def check(self, **overrides):
        kwargs = dict(
            version=(3, 13, 7), machine="arm64", host="arm64",
            clang=Path("/clang"), clang_archs=["arm64"], venv=self.venv,
        )
        kwargs.update(overrides)
        return env_check.check(**kwargs)

    def test_healthy_venv_passes(self):
        self.cfg(self.make_base())
        report = self.check()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["venv"]["base_exists"])

    def test_missing_base_interpreter_fails_with_remove_instruction(self):
        self.cfg(self.dir / "gone" / "bin")
        report = self.check()
        self.assertEqual(codes(report), ["venv_base_missing"])
        self.assertIn("rm -rf", report["problems"][0]["fix"])

    def test_directory_without_pyvenv_cfg_fails(self):
        report = self.check()
        self.assertEqual(codes(report), ["venv_broken"])

    def test_rosetta_venv_names_the_environment(self):
        self.cfg(self.make_base())
        report = self.check(machine="x86_64", clang_archs=["x86_64", "arm64"])
        self.assertEqual(codes(report), ["rosetta", "venv_arch"])


class TestCLI(unittest.TestCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = env_check.main(list(argv))
        return code, out.getvalue()

    def test_running_interpreter_is_reported(self):
        code, out = self.run_cli("--json", "--host-arch", env_check.host_arch())
        report = json.loads(out)
        self.assertEqual(report["python"], sys.executable)
        self.assertEqual(report["machine"], env_check.host_arch())
        self.assertNotIn("rosetta", codes(report))
        self.assertEqual(code, 0 if report["ok"] else 1)

    def test_forced_host_mismatch_fails_with_rosetta(self):
        other = "x86_64" if env_check.host_arch() == "arm64" else "arm64"
        code, out = self.run_cli("--json", "--host-arch", other)
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("rosetta", codes(report))

    def test_text_report_names_the_fix(self):
        other = "x86_64" if env_check.host_arch() == "arm64" else "arm64"
        code, out = self.run_cli("--host-arch", other)
        self.assertEqual(code, 1)
        self.assertIn("PROBLEM [rosetta]", out)
        self.assertIn("fix:", out)
        self.assertIn("result: incompatible", out)


if __name__ == "__main__":
    unittest.main()
