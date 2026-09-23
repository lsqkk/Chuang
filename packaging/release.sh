#!/usr/bin/env bash
# 一键发版：校验 → 打 tag → GitHub Actions 打包并把 .deb 挂到 Release 上
#
#   ./packaging/release.sh              # 推荐：走 CI，打完 tag 推上去，等它把 .deb 传好
#   ./packaging/release.sh --local      # 不等 CI：本地打包，用 gh 直接建 Release 并上传
#   ./packaging/release.sh --dry-run    # 只把该查的查一遍，什么都不改
#   ./packaging/release.sh --yes        # 不再问"继续吗"（给脚本/智能体用）
#
# 版本号取自 chuang/__init__.py（唯一一处），Release 说明取自 CHANGELOG.md。
# 需要：gh 已登录（`gh auth login`），而且 git 能推 origin（`gh auth setup-git` 一条搞定）。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SRC"

MODE="ci"            # ci | local
DRY_RUN=0
ASSUME_YES=0
ALLOW_DIRTY=0
BRANCH="master"
WAIT_MINUTES="${WAIT_MINUTES:-20}"
WORKFLOW="release.yml"

say() { printf '%s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }
repo_url() { git remote get-url origin | sed 's/\.git$//'; }

usage() { sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --local) MODE="local" ;;
    --dry-run|-n) DRY_RUN=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --branch) shift; BRANCH="${1:-}" ;;
    -h|--help) usage; exit 0 ;;
    *) die "不认识的参数：$1（--help 看用法）" ;;
  esac
  shift
done

# ---------------------------------------------------------------- 版本与前置检查

VERSION="$(python3 - "$SRC" <<'PY'
import re, sys, pathlib
text = (pathlib.Path(sys.argv[1]) / "chuang" / "__init__.py").read_text(encoding="utf-8")
print(re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1))
PY
)"
TAG="v$VERSION"

[ -d .git ] || die "这里不是 git 仓库"

CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$CUR_BRANCH" = "$BRANCH" ] || die "当前在 $CUR_BRANCH 分支，发版要在 $BRANCH 上"

if [ "$ALLOW_DIRTY" = 0 ] && [ -n "$(git status --porcelain)" ]; then
  die "工作树不干净，先提交或 stash（确实想带脏工作树发版就加 --allow-dirty）"
fi

say "▸ 检查远端状态"
GIT_TERMINAL_PROMPT=0 timeout 60 git fetch --tags --prune origin \
  || die "取不到远端（git 没配推/拉凭据？跑一次 gh auth setup-git）"

TAG_EXISTS=0
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null \
   || git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
  TAG_EXISTS=1
fi
if [ "$TAG_EXISTS" = 1 ]; then
  if [ "$MODE" = "ci" ]; then
    die "$TAG 已经存在。要重传附件 / 改 Release 说明，用：$0 --local"
  fi
  say "⚠ $TAG 已经存在，--local 会覆盖 Release 上的附件"
fi

HEAD_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo "")"
if [ "$HEAD_SHA" != "$REMOTE_SHA" ]; then
  die "本地 $BRANCH 还没推到 origin，先把代码 push 上去（Release 要挂到远端那个提交上）"
fi

if [ "$TAG_EXISTS" = 0 ]; then
  # 和 CI 用同一套判断（packaging/ci-release-plan.py），免得两边各有一套规则
  PLAN="$(python3 packaging/ci-release-plan.py --event push --ref "refs/heads/$BRANCH")"
  [ "$(printf '%s\n' "$PLAN" | sed -n 's/^release=//p')" = "true" ] \
    || die "$(printf '%s\n' "$PLAN" | sed -n 's/^reason=//p')"
fi

NOTES_OK=1
if ! python3 packaging/release-notes.py "$VERSION" >/dev/null 2>&1; then
  NOTES_OK=0
  say "⚠ CHANGELOG.md 里没有 $VERSION 的段落，Release 说明会退成一行链接"
fi

say ""
say "  版本号    $VERSION"
say "  tag       $TAG"
say "  提交      $HEAD_SHA"
say "  方式      $([ "$MODE" = ci ] && echo 'CI 自动打包上传（push tag）' || echo '本地打包 + gh 直接上传')"
say "  说明      $([ "$NOTES_OK" = 1 ] && echo "CHANGELOG.md 的 [$VERSION] 段落" || echo '（缺 CHANGELOG 段落）')"
say ""

if [ "$DRY_RUN" = 1 ]; then
  say "✓ 检查都过了（--dry-run，什么都没改）"
  exit 0
fi

command -v gh >/dev/null 2>&1 || die "没装 GitHub CLI（gh）。安装：\
见 https://github.com/cli/cli#installation，或 sudo apt install gh"
gh auth status >/dev/null 2>&1 || die "gh 还没登录，先跑一次：gh auth login"

if [ "$ASSUME_YES" = 1 ]; then
  :
elif [ -t 0 ]; then
  read -r -p "→ 就这么发？[y/N] " ans
  case "$ans" in y|Y|yes|YES) ;; *) say "好，什么都没做"; exit 1 ;; esac
else
  die "非交互环境请在命令里加 --yes"
fi

# ---------------------------------------------------------------- 本机打包（--local）

build_deb() {
  say "▸ 本地打包"
  bash packaging/build-deb.sh
  DEB="packaging/out/chuang_${VERSION}_all.deb"
  [ -f "$DEB" ] || die "打出来的包不在预期位置：$DEB"
  ( cd packaging/out && sha256sum "chuang_${VERSION}_all.deb" > SHA256SUMS )
}

write_notes() {
  local out="$1"
  if python3 packaging/release-notes.py "$VERSION" > "$out" 2>/dev/null; then
    return 0
  fi
  printf '本次改动见 [CHANGELOG.md](%s/blob/%s/CHANGELOG.md)。\n' \
    "$(repo_url)" "$HEAD_SHA" > "$out"
}

# ---------------------------------------------------------------- 等 CI 把 .deb 传上去

find_run_id() {
  local i
  for i in $(seq 1 30); do          # 最多等 2.5 分钟让 run 出现
    local id
    id="$(gh run list --workflow "$WORKFLOW" --limit 20 \
            --json databaseId,event,headBranch,status \
            --jq "[.[] | select(.event==\"push\" and .headBranch==\"$TAG\")][0].databaseId" \
          2>/dev/null || true)"
    if [ -n "$id" ] && [ "$id" != "null" ]; then
      printf '%s' "$id"
      return 0
    fi
    sleep 5
  done
  return 1
}

verify_release() {
  local tmp deb got
  tmp="$(mktemp -d)"
  say "▸ 取一份 Release 上的 .deb 下来验一下"
  if ! gh release download "$TAG" --pattern '*.deb' --dir "$tmp" --clobber >/dev/null 2>&1; then
    rm -rf "$tmp"
    die "Release 上没找到 .deb"
  fi
  deb="$(ls "$tmp"/*.deb | head -1)"
  if command -v dpkg-deb >/dev/null 2>&1; then
    got="$(dpkg-deb -f "$deb" Version)"
    [ "$got" = "$VERSION" ] || { rm -rf "$tmp"; die "Release 里的 deb 版本是 $got，不是 $VERSION"; }
    dpkg-deb -c "$deb" >/dev/null || { rm -rf "$tmp"; die "Release 里的 deb 结构坏了"; }
  fi
  say "✓ Release 里的 $(basename "$deb") 自检通过（$(du -h "$deb" | cut -f1)）"
  rm -rf "$tmp"
}

release_url="$(repo_url)/releases/tag/$TAG"

if [ "$MODE" = "ci" ]; then
  say "▸ 打 tag 并推上去"
  git tag -a "$TAG" -m "窗 $TAG"
  GIT_TERMINAL_PROMPT=0 git push origin "refs/tags/$TAG" \
    || die "推 tag 失败（凭据没配好？跑一次 gh auth setup-git 再来）"

  say "▸ 等 GitHub Actions 打包上传（$WORKFLOW）"
  RUN_ID="$(find_run_id)" || die "没等到 CI 的 run，去 Actions 页面看看：$(repo_url)/actions"
  set +e
  timeout $((WAIT_MINUTES * 60)) gh run watch "$RUN_ID" --interval 10 --exit-status >/dev/null
  rc=$?
  set -e
  if [ "$rc" = 124 ]; then
    say "✗ 等了 ${WAIT_MINUTES} 分钟还没跑完，自己去看看：$(repo_url)/actions/runs/$RUN_ID"
    exit 1
  fi
  if [ "$rc" != 0 ]; then
    say "✗ CI 失败了，日志：$(repo_url)/actions/runs/$RUN_ID"
    say "  修好后可以重跑：gh run rerun $RUN_ID"
    exit 1
  fi
else
  build_deb
  NOTES="$(mktemp)"
  write_notes "$NOTES"
  if gh release view "$TAG" >/dev/null 2>&1; then
    say "▸ Release $TAG 已存在，覆盖上传"
    gh release upload "$TAG" "$DEB" packaging/out/SHA256SUMS --clobber
  else
    say "▸ 建 Release $TAG 并上传"
    gh release create "$TAG" "$DEB" packaging/out/SHA256SUMS \
      --title "$TAG" --notes-file "$NOTES" --target "$HEAD_SHA" --latest
  fi
  rm -f "$NOTES"
  git fetch --tags --quiet origin || true    # 把 gh 刚创建的那个 tag 拉回来
fi

verify_release

say ""
say "✓ $TAG 发布完成：$release_url"
say "  安装：sudo dpkg -i chuang_${VERSION}_all.deb"
