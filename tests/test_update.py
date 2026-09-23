"""检查更新与安装包校验（纯标准库，不联网也能跑）。"""

import tempfile
import unittest
from pathlib import Path

from chuang import update as U


class TestVersion(unittest.TestCase):

    def test_parse_version(self):
        self.assertEqual(U.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(U.parse_version("1.2"), (1, 2))
        self.assertEqual(U.parse_version("  V2.0.10 "), (2, 0, 10))
        self.assertEqual(U.parse_version("1.2.3-beta.1"), (1, 2, 3))
        self.assertEqual(U.parse_version(""), ())
        self.assertEqual(U.parse_version("abc"), ())

    def test_is_newer(self):
        self.assertTrue(U.is_newer((1, 2), (1, 1, 9)))
        self.assertTrue(U.is_newer((2, 0, 0), (1, 9, 9)))
        self.assertFalse(U.is_newer((1, 1, 7), (1, 1, 7)))
        self.assertFalse(U.is_newer((1, 1), (1, 1, 0)))
        self.assertFalse(U.is_newer((), (1, 1, 7)))          # 拿不到版本就别说有新版本


class TestSha256(unittest.TestCase):

    SAMPLE = (
        "d2b2f0e2b6e6c0d6b1a2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718  chuang_1.1.8_all.deb\n"
        "\n"
        "# 注释行会被跳过\n"
        "zzz  not-a-digest.deb\n"
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef *other.deb\n"
    )

    def test_parse(self):
        table = U.parse_sha256sums(self.SAMPLE)
        self.assertEqual(table["chuang_1.1.8_all.deb"],
                         "d2b2f0e2b6e6c0d6b1a2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718")
        self.assertEqual(table["other.deb"], "0123456789abcdef0123456789abcdef"
                                             "0123456789abcdef0123456789abcdef")
        self.assertNotIn("not-a-digest.deb", table)
        self.assertEqual(len(table), 2)

    def test_parse_takes_basename(self):
        table = U.parse_sha256sums(
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  ./out/x.deb\n")
        self.assertIn("x.deb", table)

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.bin"
            p.write_bytes(b"chuang")
            # printf 'chuang' | sha256sum 的结果
            self.assertEqual(
                U.sha256_file(p),
                "e175f9541ebaf21fc7a14e844918576a0c752e8a3cda53283de3d8170992b0fd")


class TestReleaseParsing(unittest.TestCase):

    def test_release_fields(self):
        rel = U.Release(tag="v1.2.0", deb_url="http://x/a.deb", deb_name="a.deb",
                        sums_url="http://x/SHA256SUMS")
        self.assertEqual(rel.sums_url, "http://x/SHA256SUMS")
        self.assertEqual(U.Release().version_text, "")
        self.assertEqual(U.Release(tag="v1.2.0", version=(1, 2, 0)).version_text, "1.2.0")


if __name__ == "__main__":
    unittest.main()
