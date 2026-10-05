#!/usr/bin/env python3
"""把源码里 `_("…")` 那些词抽成 .pot，再把 .po 编成运行时要读的 .mo。

**为什么不用 xgettext / msgfmt**：这台机器（以及 CI 的 ubuntu-22.04）上
`gettext` 工具不一定是装着的，而「窗」的规矩是"只用标准库"。所以抽取与编译
都在这里用 Python 自己实现——本来 .mo 也只是几个长度前缀的字符串表。

    python3 tools/i18n.py extract     # 扫源码 → po/chuang.pot
    python3 tools/i18n.py compile     # po/*.po → chuang/locale/<语言>/LC_MESSAGES/
    python3 tools/i18n.py check       # 源码里的词在不在各语言里
    python3 tools/i18n.py stats       # 各语言翻了多少（按"词"算）

改界面上的字之后：先 `extract` 把新词并进各 `po/`，翻好，再 `compile`。
`chuang/locale/` 下的 `.mo` 要一起提交——开发时直接跑 `./chuang-gui` 读的就是它。
"""

from __future__ import annotations

import argparse
import ast
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "chuang"
PO_DIR = ROOT / "po"
DOMAIN = "chuang"
LOCALE_DIR = PKG / "locale"
POT = PO_DIR / f"{DOMAIN}.pot"
FALLBACK_LANG = "en"       # 兜底语言：英文（见 chuang/i18n.py）

# 抽取时认这几个名字：`_` 是 `tr` 的别名，`ntr` 是复数，`ctr` 是带语境
SINGULAR = {"_", "tr", "N_"}
PLURAL = {"ntr", "ngettext"}
CONTEXT = {"ctr", "pgettext"}


# --------------------------------------------------------------------------
# 抽取
# --------------------------------------------------------------------------

def _string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def extract():
    """扫一遍源码，返回（普通词, 复数词）两张表。

    普通词按语境分组（语境为空就是没有语境的那些），复数词是
    `{语境: {(单数, 复数), …}}`。
    """
    plain: dict[str, set[str]] = {}
    plural: dict[str, set[tuple[str, str]]] = {}
    files = sorted(PKG.rglob("*.py")) + sorted((ROOT / "tools").glob("*.py"))
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else \
                (func.attr if isinstance(func, ast.Attribute) else "")
            args = [_string(a) for a in node.args]
            if name in SINGULAR and args and args[0]:
                plain.setdefault("", set()).add(args[0])
            elif name in PLURAL and len(args) >= 2 and args[0] and args[1]:
                plural.setdefault("", set()).add((args[0], args[1]))
            elif name in CONTEXT and len(args) >= 2 and args[0] and args[1]:
                plain.setdefault(args[0], set()).add(args[1])
    return plain, plural


def _po_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r"))


def write_pot(plain, plural) -> None:
    PO_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 「窗 · Chuang」的翻译模板。",
        "# 由 `python3 tools/i18n.py extract` 生成；msgid 是代码里写的中文原文。",
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: chuang\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
    ]
    for context in sorted(plain):
        for msgid in sorted(plain[context]):
            if context:
                lines.append(f'msgctxt "{_po_escape(context)}"')
            lines.append(f'msgid "{_po_escape(msgid)}"')
            lines.append('msgstr ""')
            lines.append("")
    for msgid, msgid_plural in sorted(plural.get("", ())):
        lines.append(f'msgid "{_po_escape(msgid)}"')
        lines.append(f'msgid_plural "{_po_escape(msgid_plural)}"')
        lines.append('msgstr[0] ""')
        lines.append('msgstr[1] ""')
        lines.append("")
    POT.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------
# 读 .po
# --------------------------------------------------------------------------

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
            "a": "\a", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class Entry:
    def __init__(self) -> None:
        self.ctxt = ""
        self.msgid = ""
        self.plural = ""
        self.strs: dict[int, str] = {}
        self.fuzzy = False


def parse_po(path: Path) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """读一份 .po：返回（单数表, 复数表）。键都带上语境前缀 `ctxt\\x04`。

    复数那条的键是"单数原文"，值是 `(复数原文, 用 \\x00 连起来的各条译文)`——
    GNU 的 .mo 里复数就是按 `msgid\\x00msgid_plural` 存一个键的。
    """
    entries: list[Entry] = []
    cur: Entry | None = None
    field = ""
    index = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            if ", fuzzy" in line and cur is not None:
                cur.fuzzy = True
            continue
        if line.startswith("msgctxt "):
            # `msgctxt` 与紧跟的 `msgid` 是同一条；只有"上一条已经有 msgid"时
            # 才是新的一条开始。
            if cur is not None and cur.msgid:
                entries.append(cur)
            if cur is None or cur.msgid:
                cur = Entry()
            field = "ctxt"
            cur.ctxt = _unquote(line[8:])
        elif line.startswith("msgid_plural "):
            field = "plural"
            assert cur is not None
            cur.plural = _unquote(line[13:])
        elif line.startswith("msgid "):
            if cur is None or cur.msgid:
                if cur is not None:
                    entries.append(cur)
                cur = Entry()
            field = "msgid"
            cur.msgid = _unquote(line[6:])
        elif line.startswith("msgstr["):
            assert cur is not None
            index = int(line[7:line.index("]")])
            field, _, rest = line.partition("]")
            cur.strs[index] = _unquote(rest)
        elif line.startswith("msgstr "):
            assert cur is not None
            field, index = "str", 0
            cur.strs[0] = _unquote(line[7:])
        elif line.startswith('"'):
            assert cur is not None
            piece = _unquote(line)
            if field == "ctxt":
                cur.ctxt += piece
            elif field == "msgid":
                cur.msgid += piece
            elif field == "plural":
                cur.plural += piece
            else:
                cur.strs[index] = cur.strs.get(index, "") + piece
    if cur is not None:
        entries.append(cur)

    singles: dict[str, str] = {}
    plurals: dict[str, tuple[str, str]] = {}
    for entry in entries:
        if not entry.msgid:
            continue                       # 头（metadata）不进查找表
        key = f"{entry.ctxt}\x04{entry.msgid}" if entry.ctxt else entry.msgid
        if entry.plural:
            parts = [entry.strs.get(i) for i in sorted(entry.strs)]
            if not any(parts):
                continue
            plurals[key] = (entry.plural, "\x00".join(parts))
        else:
            value = entry.strs.get(0, "")
            if value:
                singles[key] = value
    return singles, plurals


def header(path: Path) -> str:
    """取 .po 头部那段 metadata（不带进查找表，但要原样写进 .mo）。"""
    text = path.read_text(encoding="utf-8")
    start = text.find('msgid ""')
    if start < 0:
        return ""
    out: list[str] = []
    for line in text[start:].splitlines()[1:]:
        if line.startswith("msgstr "):
            out.append(_unquote(line[7:]))
        elif line.startswith('"'):
            out.append(_unquote(line))
        elif line.strip() and not line.lstrip().startswith("#"):
            break
    return "".join(out)


# --------------------------------------------------------------------------
# 写 .mo
# --------------------------------------------------------------------------

def build_mo(table: dict[bytes, bytes]) -> bytes:
    """把一张 `{原文: 译文}` 表写成 GNU .mo（小端、UTF-8）。

    注意：字符串表里的偏移是**从文件开头算的绝对位置**（GNU 的约定，
    Python 的 `gettext` 也这么读）——写成"相对数据段"的偏移会让它把表头
    当译文去 decode，报一个看不懂的 UnicodeDecodeError。
    """
    keys = sorted(table)
    n = len(keys)
    header_size = 7 * 4
    key_table_off = header_size
    value_table_off = key_table_off + n * 8
    data_off = value_table_off + n * 8
    # 数据段分两截：先是所有原文，再是所有译文；偏移一律从文件开头算。
    key_data = b"".join(key + b"\0" for key in keys)
    val_data = b"".join(table[key] + b"\0" for key in keys)
    rows = []
    koff = data_off
    voff = data_off + len(key_data)
    for key in keys:
        rows.append((len(key), koff, len(table[key]), voff))
        koff += len(key) + 1
        voff += len(table[key]) + 1
    out = bytearray(struct.pack("<Iiiiiii", 0x950412DE, 0, n,
                                key_table_off, value_table_off, 0, data_off))
    for klen, koff, _vlen, _voff in rows:
        out += struct.pack("<ii", klen, koff)
    for _klen, _koff, vlen, voff in rows:
        out += struct.pack("<ii", vlen, voff)
    out += key_data + val_data
    return bytes(out)


def compile_all() -> int:
    if not PO_DIR.exists():
        print(f"没有找到 {PO_DIR}", file=sys.stderr)
        return 1
    count = 0
    for po in sorted(PO_DIR.glob("*.po")):
        lang = po.stem
        singles, plurals = parse_po(po)
        meta = header(po) or (
            "MIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\n"
            "Content-Transfer-Encoding: 8bit\nPlural-Forms: nplurals=2; "
            "plural=(n != 1);\n")
        mo: dict[bytes, bytes] = {b"": meta.encode("utf-8")}
        for key, value in singles.items():
            mo[key.encode("utf-8")] = value.encode("utf-8")
        for key, (msgid_plural, joined) in plurals.items():
            mo[(key + "\x00" + msgid_plural).encode("utf-8")] = joined.encode("utf-8")
        data = build_mo(mo)
        target = LOCALE_DIR / lang / "LC_MESSAGES" / f"{DOMAIN}.mo"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        count += 1
        print(f"  {lang}: {len(singles)} 条单数 / {len(plurals)} 条复数 → "
              f"{target.relative_to(ROOT)}")
    print(f"✓ 编好 {count} 份目录")
    return 0


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------

def _wanted() -> set:
    plain, plural = extract()
    wanted = {m for group in plain.values() for m in group}
    wanted |= {m for a, b in plural.get("", ()) for m in (a, b)}
    return wanted


def check() -> int:
    """检查各语言的 .po。

    两种毛病要分开看：**stale**（.po 里有一个源码里已经没有的词）是硬错误——
    它多半意味着原文改了而译文没跟上，那条译文永远不会再被用到；**missing**
    只是"这门语言还没翻到那儿"，少的部分会自动退回英文（见 chuang/i18n.py），
    所以只报覆盖率。英文是兜底，缺了就是真的缺，单独要求它 100%。
    """
    wanted = _wanted()
    problems = 0
    for po in sorted(PO_DIR.glob("*.po")):
        singles, plurals = parse_po(po)
        have = {key.split("\x04")[-1] for key in singles}
        for key, (msgid_plural, _joined) in plurals.items():
            have.add(key.split("\x04")[-1])
            have.add(msgid_plural)
        missing = wanted - have
        stale = have - wanted
        if missing:
            pct = 100.0 * (len(wanted) - len(missing)) / len(wanted or [1])
            level = "✗" if po.stem == FALLBACK_LANG else "·"
            print(f"{level} {po.name}: 覆盖率 {pct:.0f}%，还差 {len(missing)} 个词，"
                  f"例如 {sorted(missing)[:3]}")
            if po.stem == FALLBACK_LANG:
                problems += 1
        if stale:
            problems += 1
            print(f"✗ {po.name}: 有 {len(stale)} 个源码里已经没有的词，例如 "
                  f"{sorted(stale)[:3]}")
        if not missing and not stale:
            print(f"✓ {po.name}: {len(have)} 个词都在")
    if not list(PO_DIR.glob("*.po")):
        print(f"✗ {PO_DIR} 里一份 .po 都没有", file=sys.stderr)
        problems += 1
    return 1 if problems else 0


def stats() -> int:
    wanted = _wanted()
    total = len(wanted)
    print(f"源码里一共 {total} 个词")
    for po in sorted(PO_DIR.glob("*.po")):
        singles, plurals = parse_po(po)
        have = {key.split("\x04")[-1] for key in singles}
        for key, (msgid_plural, _joined) in plurals.items():
            have.add(key.split("\x04")[-1])
            have.add(msgid_plural)
        pct = (100.0 * len(have) / total) if total else 0.0
        print(f"  {po.stem}: {len(have)}/{total} ({pct:.0f}%)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("extract", "compile", "check", "stats"))
    args = parser.parse_args(argv)
    if args.action == "extract":
        plain, plural = extract()
        write_pot(plain, plural)
        print(f"✓ 写好 {POT.relative_to(ROOT)}（{len(_wanted())} 个词）")
        return 0
    if args.action == "compile":
        return compile_all()
    if args.action == "check":
        return check()
    return stats()


if __name__ == "__main__":
    raise SystemExit(main())
