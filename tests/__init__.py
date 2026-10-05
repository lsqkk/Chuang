"""「窗」的自动化测试。

只用标准库的 unittest，不引入任何第三方依赖（项目和测试都守这条线）。
跑法：
    python3 -m unittest discover -s tests -t . -v
    make test
"""

import os

# 界面语言的**确定性**：1.3.0 起界面会跟着系统语言走，而 CI 的 LANG 与本机
# 不一样（本机 zh_CN、CI 多半是 C.UTF-8）。测试里断言的都是中文原文，所以
# 在这里钉死成中文——测多语言的那几条自己调用 i18n.setup() 换过去再换回来。
os.environ.setdefault("CHUANG_LANG", "zh")
