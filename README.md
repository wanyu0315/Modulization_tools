# check_head_tools

## 简介

`check_head_tools` 是一套面向大型 C/C++ 工程的冗余头文件治理工具集，目标是把“发现疑似无用 `#include`”到“真实编译验证是否可删”这两类工作拆成多个独立阶段，便于按场景组合使用。

这套工具主要覆盖三类能力：

1. **静态扫描**：基于 `clang-tidy` 或 JetBrains ReSharper CLT，定位疑似冗余头文件。
2. **结果结构化**：将原始扫描输出整理为统一 CSV 清单，便于后续自动化处理。
3. **物理编译验真**：批量注释候选 `#include` 并调用 `MSBuild` 做真实构建，过滤误报。

---

## 目录结构

### 1. [Clang-tidy](./Clang-tidy)

基于 `clang-tidy` 的冗余头文件并发扫描工具链。

- `generate_compile_database.py`：生成 `compile_commands.json`
- `paralled.py`：并发调度 `clang-tidy`，集中扫描源码目录
- `dealtxt.py`：从扫描日志中提取 `misc-include-cleaner` 结果，输出统一 CSV
- `tidy_check_headers.cmd`：Windows 下的串联入口脚本

适用场景：

- 已具备 CMake / `compile_commands.json` 环境
- 需要快速扫全量或单个模块
- 希望优先使用开源工具链

详细说明见：[Clang-tidy/README.md](/home/developer/modulization_tools/check_head_tools/Clang-tidy/README.md)

### 2. [Resharper](./Resharper)

基于 JetBrains ReSharper Command Line Tools 的冗余头文件扫描方案说明。

该方案强调对复杂 C++ 宏、模板和工程上下文的解析能力，适合作为另一条静态扫描链路，用于和 `clang-tidy` 结果交叉比对，或者在特定工程配置下获取更稳定的候选清单。

当前目录下主要提供方法说明文档，介绍了：

- 如何配置 `CppUnusedIncludeDirective`
- 如何使用 `inspectcode.exe` 生成 XML 报告
- 如何将结果进一步转成统一 CSV 结构

详细说明见：[Resharper/README.md](/home/developer/modulization_tools/check_head_tools/Resharper/README.md)

### 3. [Build](./Build)

基于真实编译的自动化验证工具链，用来确认“扫描出来的候选头文件”是否真的可以安全删除。

- `fast_build.py`：增量双端构建快筛，先快速过滤明显误报
- `batch_rebuild.py`：基于二分逼近的全局 `Rebuild` 验证，适合处理跨模块隐式依赖
- `comparecvs.py`：比较两份 CSV 的交集、差集和并集

适用场景：

- 需要把静态扫描结果转成可落地的安全清理清单
- 需要在提交前做更严格的编译验证
- 需要比较不同扫描引擎或不同分支的结果差异

详细说明见：[Build/README.md](/home/developer/modulization_tools/check_head_tools/Build/README.md)

---

## 推荐工作流

### 工作流 A：日常开发快速清理

适合单模块或小范围重构。

1. 使用 `Clang-tidy` 或 `Resharper` 生成疑似冗余头文件清单。
2. 将结果整理为统一 CSV。
3. 运行 `Build/fast_build.py` 做增量双端构建快筛。
4. 对快筛通过的结果再运行 `Build/batch_rebuild.py` 做严格验证。

特点：

- 速度快
- 适合开发阶段反复执行
- 能较好平衡效率和安全性

### 工作流 B：多引擎交叉扫描后统一验证

适合大规模治理或版本冻结前的集中清理。

1. 分别运行 `Clang-tidy` 与 `Resharper` 两条扫描链路。
2. 使用 `Build/comparecvs.py` 对两份 CSV 做交集 / 并集分析。
3. 以交集结果作为高置信候选，或以并集结果作为全量候选。
4. 使用 `fast_build.py` 和 `batch_rebuild.py` 做分层验证。

特点：

- 结果更稳健
- 适合全局治理和回归比对
- 可以观察不同扫描引擎的覆盖差异

---

## 输入输出约定

为了让扫描链路和编译验证链路能够无缝衔接，推荐统一使用如下 CSV 结构：

| 列名 | 含义 |
| --- | --- |
| `Submodule` | 子模块名 |
| `File Path` | 源码相对路径 |
| `Line Number` | 冗余 `#include` 所在行号 |
| `Code Line` | 对应源码行内容 |

`Clang-tidy/dealtxt.py` 与 `Build/comparecvs.py` 默认都围绕这套字段工作，`Build` 下的脚本也以此作为主要输入格式。

---

## 环境要求

- Windows 开发环境
- Python 3
- Visual Studio / `MSBuild.exe`
- 若使用 `Clang-tidy` 链路，还需要：
  - `clang-tidy.exe`
  - `compile_commands.json`
- 若使用 `Resharper` 链路，还需要：
  - JetBrains ReSharper Command Line Tools

---

## 适用建议

- 如果你只想**尽快扫出候选列表**，优先看 `Clang-tidy`。
- 如果你更关注**复杂 C++ 工程语义解析能力**，可以补充使用 `Resharper`。
- 如果你要把“疑似冗余”推进到“可安全提交的代码修改”，核心在 `Build`。

这三个子模块并不是互斥关系，而是可以组合成一条完整的冗余头文件治理流水线。
