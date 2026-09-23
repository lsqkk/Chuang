"""自动更新那条链路里最要紧的一步：装之前先把 .deb 校验一遍。

这一条是"拿 root 装一个从网上拿来的包"的整个流程里唯一的完整性证据，
所以既要测"对的包放行"，也要测"错的包拦住、没有校验文件也拦住"。

测试用 `file://` 当 Release 地址，完全不联网；不导入 GTK 的环境里自动跳过。
"""

import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote

try:
    from chuang import app as appmod          # 需要 gi / GTK4 / libadwaita
    from chuang import update as upmod
    IMPORT_ERROR = None
except Exception as exc:                      # pragma: no cover - 看环境
    IMPORT_ERROR = exc


def _file_url(path: Path) -> str:
    return "file://" + quote(str(path))


@unittest.skipIf(IMPORT_ERROR is not None, f"没有 GTK4/libadwaita：{IMPORT_ERROR}")
class TestVerifyDeb(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.deb = root / "chuang_1.1.8_all.deb"
        self.deb.write_bytes(b"this stands for a .deb")
        digest = upmod.sha256_file(self.deb)
        self.sums = root / "SHA256SUMS"
        self.sums.write_text(
            f"{digest}  chuang_1.1.8_all.deb\n"
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  other.deb\n",
            encoding="utf-8")

    def _release(self, sums: Path | None):
        return upmod.Release(tag="v1.1.8", deb_name=self.deb.name,
                             deb_url=_file_url(self.deb),
                             sums_url=_file_url(sums) if sums else "")

    def test_accepts_matching_digest(self):
        ok, why = appmod.ChuangWindow._verify_deb(None, self._release(self.sums), self.deb)
        self.assertTrue(ok, why)
        self.assertEqual(why, "")

    def test_rejects_tampered_package(self):
        self.deb.write_bytes(b"this stands for a TAMPERED .deb")
        ok, why = appmod.ChuangWindow._verify_deb(None, self._release(self.sums), self.deb)
        self.assertFalse(ok)
        self.assertIn("校验不通过", why)
        self.assertIn(upmod.sha256_file(self.deb), why)      # 两个摘要都要摆出来

    def test_rejects_when_no_sums_asset(self):
        ok, why = appmod.ChuangWindow._verify_deb(None, self._release(None), self.deb)
        self.assertFalse(ok)
        self.assertIn("SHA256SUMS", why)

    def test_rejects_when_deb_not_listed(self):
        self.sums.write_text("0123456789abcdef0123456789abcdef0123456789abcdef"
                             "0123456789abcdef  something-else.deb\n", encoding="utf-8")
        ok, why = appmod.ChuangWindow._verify_deb(None, self._release(self.sums), self.deb)
        self.assertFalse(ok)
        self.assertIn("没有", why)

    def test_unreachable_sums_is_a_refusal(self):
        ok, why = appmod.ChuangWindow._verify_deb(
            None, self._release(Path(self.tmp.name) / "nope"), self.deb)
        self.assertFalse(ok)
        self.assertIn("失败", why)


if __name__ == "__main__":
    unittest.main()
