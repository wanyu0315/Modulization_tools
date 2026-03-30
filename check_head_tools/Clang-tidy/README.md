# 冗余头文件并发扫描工具 (Clang-Tidy Multi-core Scanner)

## 简介

本工具链用于在 ZW3D 大型 C++ 工程中，快速、精准地扫描出未使用的冗余头文件（Redundant Includes）。

基于底层的 clang-tidy 工具，通过 Python 的 ThreadPoolExecutor 实现了多核并发扫描，彻底取代了原生 .cmd 脚本中低效的单线程 for 循环。

扫描速度相比传统串行方式提升了数十倍，极大缩短了全量代码分析的等待时间。

## 工作流管道 (Pipeline)

本工具链由多个脚本接力完成，执行顺序如下：

1. 构建地图： generate_compile_database.py 调用 CMake，生成全局编译数据库 compile_commands.json。

2. 并发扫描： parallel_tidy.py 榨干 CPU 核心，并发调度 clang-tidy.exe 扫描源码，安全写入日志。

3. 精准提纯： dealtxt.py 读取海量日志，精准过滤出 [misc-include-cleaner] 错误，并导出为格式化的 CSV 表格。

## 核心文件说明

1. tidy_check_headers.cmd (入口脚本)

这是整个工具链的总控开关。

作用： 依次调用上述三个 Python 脚本，传递路径参数，统筹全局流程。

注意： 该脚本内部不再包含具体的检查规则参数。为了避免 Windows 命令行的特殊字符转义问题，核心扫描规则被移交给了 parallel_tidy.py 处理。

2. parallel_tidy.py (高并发调度引擎)

这是本次改造的核心引擎。

作用： 自动遍历指定目录下的所有 .c, .cpp, .h, .hpp 文件，并分配给多线程并发扫描。

硬编码规则： 脚本内部（第 13 行附近）已硬编码 -checks=-*,misc-include-cleaner。这确保了机器在扫描时强制关闭其他耗时的规则，仅检查冗余头文件。

线程安全： 引入了 threading.Lock()，确保多个核心同时向同一个 txt 报告写入数据时不会发生文字穿插错乱。

## 如何使用

打开 Windows 命令行 (CMD 或 PowerShell)。

定位到当前脚本所在目录。

执行以下命令：

    .\tidy_check_headers.cmd <项目根目录路径> <要扫描的模块路径>

示例 (局部模块扫描): 

    .\tidy_check_headers.cmd "D:\ZW3D\zw3d_productional_architecture" "D:\ZW3D\zw3d_productional_architecture\math_core"

示例 (全量扫描):

    .\tidy_check_headers.cmd "D:\ZW3D\zw3d_productional_architecture" "D:\ZW3D\zw3d_productional_architecture"

## 输出结果

执行完毕后，当前目录下会生成两个重要文件：

1. tidy_result.txt：并发扫描生成的原始详细日志（通常不需要看）。

2. output.csv：这是最终的提纯报告。 用 Excel 打开，可直观看到哪个文件的第几行包含了无用的 #include。


## 高阶配置：调整 CPU 核心占用数

如果你在扫描期间需要保留一定的电脑算力用于其他工作，可以限制工具使用的 CPU 核心数。

修改方法：

用文本编辑器打开 parallel_tidy.py

找到代码（约第 59 行）：

    max_workers = os.cpu_count()

将其修改为你期望分配的具体核心数，例如限制为 20 个核心：

    max_workers = 20

保存即可生效。