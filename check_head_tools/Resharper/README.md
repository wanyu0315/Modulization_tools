冗余头文件扫描与解析工具链 (JetBrains 版)

简介

本工具链主要用于 C++ 项目的架构瘦身与编译加速。它基于 **JetBrains ReSharper Command Line Tools (CLT)** 进行深度静态代码分析，定位项目中包含但未使用的冗余头文件 (`#include`)，并通过专门的 Python 脚本将其 XML 报告转换为结构化的 CSV 物理文件清单，以便无缝对接后续的自动化编译验证（`batch_rebuild.py` 等）。

本方案具有**不依赖正则表达式**、**精准度极高**、**原生支持复杂 C++ 宏与模板解析**的特点。

------

**阶段一：使用 JetBrains InspectCode 生成报告**

1. 工具准备

确保你的机器上已下载并解压了 [JetBrains ReSharper Command Line Tools](https://www.jetbrains.com/resharper/features/command-line.html) (免费工具)。核心执行文件为 `inspectcode.exe`。

2. 核心规则配置 (`.DotSettings`)

为了让引擎精准抓取冗余头文件，我们需要在工程的 `.DotSettings` 配置文件中，将 `CppUnusedIncludeDirective` 规则的严重级别提升为 `ERROR`。

确保你的配置文件中包含以下内容：

```XML
<wpf:ResourceDictionary xml:space="preserve" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" xmlns:s="clr-namespace:System;assembly=mscorlib" xmlns:ss="urn:schemas-jetbrains-com:settings-storage-xaml" xmlns:wpf="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
  <s:String x:Key="/Default/CodeInspection/Highlighting/InspectionSeverities/CppUnusedIncludeDirective/@EntryValue">ERROR</s:String>
</wpf:ResourceDictionary>
```

3. 执行扫描命令

在终端中调用 `inspectcode.exe` 对解决方案进行扫描，并将结果输出为 XML 文件：

```
inspectcode.exe "D:\ZW3D\zw3d_productional_architecture\out\build\0_MSBuild\ZW3D.sln" --profile="你的配置文件.DotSettings" --output="report.xml"
```

*(注：由于项目庞大，此步骤可能需要较长时间，建议在夜间或使用高性能机器运行。)*

------

阶段二：使用 `xml_to_csv.py` 提取与解析

1. 脚本定位

JetBrains 生成的 `report.xml` 包含了数百种不同类型的代码规范检查结果，且路径通常为相对路径。`xml_to_csv.py` 利用原生 XML DOM 树进行结构化解析，精准剥离出冗余头文件记录，并结合本地物理文件提取具体代码行。

2. 参数说明

| **参数**         | **必填** | **说明**                                                     | **示例**                                      |
| ---------------- | -------- | ------------------------------------------------------------ | --------------------------------------------- |
| `-i`, `--input`  | ✅        | JetBrains 生成的 XML 报告文件路径。                          | `-i report.xml`                               |
| `-o`, `--output` | ❌        | 解析后生成的 CSV 保存路径（默认：`output.csv`）。            | `-o clean_headers.csv`                        |
| `-p`, `--prefix` | ✅        | **极度重要！** 本地源码的根目录绝对路径。用于与 XML 中的相对路径拼接，从而让脚本能够打开真实的 `.cpp` 文件提取代码行。 | `-p "D:\ZW3D\zw3d_productional_architecture"` |

3. 执行解析

打开终端，运行以下命令（请根据实际路径替换）： 

```
python xml_to_csv.py -i report.xml -o output.csv -p "D:\ZW3D\zw3d_productional_architecture"
```

4. 产出物 (`output.csv`)

执行成功后，你将获得一个标准的 `output.csv` 表格，包含以下四列数据，完美对接后续的自动化编译工具：

1. **Submodule**: 发生错误的子模块名（例如 `math_core`）。
2. **File Path**: 源码文件的相对路径。
3. **Line Number**: 冗余 `#include` 所在的精确行号。
4. **Code Line**: 物理文件中的真实代码内容（如 `#include <vector>`）。