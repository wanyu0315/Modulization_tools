import csv
import os
import shutil
import subprocess
import argparse
import time
import signal
import atexit
import tempfile
from datetime import datetime
from itertools import groupby


_active_backups = {}
_cleanup_in_progress = False
_consecutive_failures = 0


# ----------------------------------------------
# 进程清理
# ----------------------------------------------
def _wait_for_processes_dead(process_names, timeout=15):
    """轮询 tasklist，尽量确认底层编译进程已经完全退出。"""
    names_lower = {name.lower() for name in process_names}
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            break

        running = {
            line.split(",")[0].strip('"').lower()
            for line in result.stdout.splitlines()
            if line.strip()
        }
        if not (names_lower & running):
            break
        time.sleep(0.5)

    time.sleep(2)


def kill_vs_processes():
    """强制终止 VS 相关进程（含子进程），并等待文件锁释放。"""
    process_names = [
        "VBCSCompiler.exe",
        "mspdbsrv.exe",
        "MSBuild.exe",
        "MSBuildTaskHost.exe",
        "cl.exe",
        "link.exe",
        "rc.exe",
        "csc.exe",
        "vbc.exe",
    ]
    try:
        for name in process_names:
            subprocess.run(
                ["taskkill", "/F", "/T", "/IM", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except Exception:
        pass
    _wait_for_processes_dead(process_names, timeout=15)


def maybe_cooldown_environment(max_failures):
    """连续失败过多时做一次重置，避免在异常环境上持续误判。"""
    global _consecutive_failures

    if _consecutive_failures < max_failures:
        return

    print(f"\n   警告: 已连续失败 {_consecutive_failures} 次，触发环境冷却保护。")
    print("   状态: 正在清理残留编译进程并等待系统资源回收 (30s)...")
    kill_vs_processes()
    time.sleep(30)
    _consecutive_failures = 0
    print("   状态: 冷却完成，继续执行。")


def touch_file(path):
    try:
        os.utime(path, None)
    except Exception:
        pass


def tail_text_file(path, max_chars=4000):
    """仅读取日志尾部，避免失败时把整份构建日志灌进控制台。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, max_chars * 4)
            if read_size < size:
                f.seek(-read_size, os.SEEK_END)
            else:
                f.seek(0, os.SEEK_SET)
            data = f.read().decode("utf-8", errors="ignore")
            return data[-max_chars:]
    except Exception:
        return ""


def default_msbuild_cpu_count():
    """默认使用本机逻辑核数的一半，至少保留 1 个构建节点。"""
    cpu_total = os.cpu_count() or 1
    return max(1, cpu_total // 2)


def register_backup(file_path, backup_path):
    _active_backups[file_path] = backup_path


def unregister_backup(file_path):
    _active_backups.pop(file_path, None)


def cleanup_active_backups():
    """恢复当前进程登记的所有活动备份，确保异常退出时源码回滚。"""
    global _cleanup_in_progress
    if _cleanup_in_progress:
        return
    _cleanup_in_progress = True
    try:
        for file_path, backup_path in list(_active_backups.items()):
            if not os.path.exists(backup_path):
                unregister_backup(file_path)
                continue
            try:
                shutil.copy2(backup_path, file_path)
                touch_file(file_path)
                os.remove(backup_path)
            except Exception as e:
                print(f"      异常: 自动回滚失败: {file_path} ({e})")
            finally:
                unregister_backup(file_path)
    finally:
        _cleanup_in_progress = False


def handle_termination_signal(signum, _frame):
    """接收中断/终止信号时，先清理编译进程，再回滚注释。"""
    print(f"\n检测到终止信号 ({signum})，正在清理编译进程并回滚已注释代码...")
    kill_vs_processes()
    cleanup_active_backups()
    raise SystemExit(128 + signum)


def install_cleanup_handlers():
    atexit.register(cleanup_active_backups)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, handle_termination_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_termination_signal)


def recover_leftover_backups(file_groups, prefix):
    """
    启动时扫描当前任务涉及的目标文件，若存在上次异常退出遗留的 .bak，则优先恢复。
    只处理本轮 CSV 涉及的文件，避免误碰无关 .bak 文件。
    """
    recovered = 0
    for file_rel_path in file_groups:
        file_path = os.path.normpath(os.path.join(prefix, file_rel_path))
        backup_path = file_path + ".bak"
        if not os.path.exists(backup_path):
            continue
        try:
            shutil.copy2(backup_path, file_path)
            touch_file(file_path)
            os.remove(backup_path)
            recovered += 1
        except Exception as e:
            print(f"警告: 启动恢复遗留备份失败: {file_path} ({e})")
    if recovered:
        print(f"恢复: 检测到并已回滚 {recovered} 个上次遗留的 .bak 备份文件。")


# ----------------------------------------------
# 构建执行
# ----------------------------------------------
def run_msbuild(
    msbuild_path, target_path, target, config, timeout, max_cpu_count,
    is_project=False, enable_binlog=False
):
    """
    执行单次 MSBuild 调用。
    - target_path: .sln 路径，或单一 .vcxproj 路径（子项目优化）
    - is_project:  True 表示为单一项目级构建，跳过全局解决方案解析
    - enable_binlog: 是否输出二进制结构化日志
    返回: (success_bool, timeout_bool)
    """
    global _consecutive_failures

    if not os.path.exists(msbuild_path) or not os.path.exists(target_path):
        return False, False

    cmd = [
        msbuild_path,
        target_path,
        f"/t:{target}",
        f"/p:Configuration={config}",
        "/p:Platform=x64",
        f"/m:{max_cpu_count}",
        "/p:BuildInParallel=true",
        "/p:MultiProcessorCompilation=true",
        "/nologo",
        "/nr:false",  # 关键修复 2：彻底关闭节点复用(NodeReuse)，防止 MSBuild 驻留后台导致文件死锁
        "/clp:ErrorsOnly;NoSummary",
    ]
    
    # 仅在需要调试排错时开启结构化日志，避免大规模执行时产生高额 I/O 阻塞
    if enable_binlog:
        cmd.append(f"/bl:msbuild_debug_{config}.binlog")

    label = os.path.basename(target_path)
    print(f"      执行: MSBuild /t:{target} /p:Configuration={config}  [{label}]")
    start_time = time.time()
    proc = None
    log_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=f".{target.lower()}.{config.lower()}.log"
        ) as log_file:
            log_path = log_file.name
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"      异常: 构建超时 ({timeout}s)")
        if proc:
            proc.kill()
            proc.wait()
        kill_vs_processes()
        _consecutive_failures += 1
        if log_path:
            tail = tail_text_file(log_path)
            if tail:
                print("      失败日志尾部:")
                print(tail)
                print(f"      日志文件: {log_path}")
        return False, True  # 返回: 构建失败, 触发超时状态
    except KeyboardInterrupt:
        # 新增保护机制：捕获 Ctrl+C 强制中断，确保后台进程被正确清理
        print("\n      警告: 检测到强制中断指令 (Ctrl+C)，正在清理底层编译进程并释放文件锁...")
        if proc:
            proc.kill()
            proc.wait()
        kill_vs_processes()
        raise  # 清理完毕后，继续向上抛出异常以安全终止 Python 主进程
    except Exception as e:
        print(f"      异常: 进程执行期间发生未知错误 ({e})")
        if proc:
            proc.kill()
            proc.wait()
        kill_vs_processes()
        _consecutive_failures += 1
        if log_path:
            tail = tail_text_file(log_path)
            if tail:
                print("      失败日志尾部:")
                print(tail)
                print(f"      日志文件: {log_path}")
        return False, False

    elapsed = time.time() - start_time
    ok = proc.returncode == 0
    mark = "成功" if ok else "失败"
    print(f"      结果: {mark} (耗时: {elapsed:.1f}s)")
    if ok:
        _consecutive_failures = 0
        if log_path and os.path.exists(log_path):
            try:
                os.remove(log_path)
            except OSError:
                pass
    else:
        _consecutive_failures += 1
        tail = tail_text_file(log_path)
        if tail:
            print("      失败日志尾部:")
            print(tail)
        if log_path:
            print(f"      日志文件: {log_path}")
    return ok, False  # 返回: 正常完成状态, 未触发超时


def run_baseline_validation(sln_path, msbuild_path, timeout, max_cpu_count, enable_binlog):
    """
    在修改任何源码前先验证基线工程本身能否完成双端 Build。
    若基线已失败，则 fast_build 的后续结论全部无效，应立即中止。
    """
    print("\n[基线验证] 先检查未修改源码时的构建环境是否健康...")
    debug_ok, _ = run_msbuild(
        msbuild_path, sln_path, "Build", "Debug", timeout, max_cpu_count,
        enable_binlog=enable_binlog
    )
    if not debug_ok:
        print("[基线验证] Debug 构建失败。当前环境或工程基线不健康，停止后续冗余头文件判定。")
        return False

    release_ok, _ = run_msbuild(
        msbuild_path, sln_path, "Build", "Release", timeout, max_cpu_count,
        enable_binlog=enable_binlog
    )
    if not release_ok:
        print("[基线验证] Release 构建失败。当前环境或工程基线不健康，停止后续冗余头文件判定。")
        return False

    print("[基线验证] Debug/Release 均通过，开始逐文件验证。")
    return True


# ----------------------------------------------
# 文件读写操作
# ----------------------------------------------
def comment_lines(file_path, line_numbers):
    """
    注释指定文件中的指定行。
    返回 (backup_path, original_lines_dict) 或 None（执行失败时）。
    original_lines_dict: {line_no: original_text}
    """
    backup = file_path + ".bak"
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        originals = {}
        for ln in line_numbers:
            if not (0 < ln <= len(lines)):
                return None
            raw = lines[ln - 1].rstrip("\n")
            if raw.strip().startswith("//") or raw.strip().startswith("/*"):
                # 目标行已被注释，记录为 None 表示跳过该行
                originals[ln] = None
            else:
                originals[ln] = raw
                lines[ln - 1] = f"// {raw}\n"

        shutil.copy2(file_path, backup)
        register_backup(file_path, backup)
        with open(file_path, "w", encoding="utf-8", errors="ignore") as f:
            f.writelines(lines)
        touch_file(file_path)

        return backup, originals
    except Exception as e:
        print(f"      异常: 注释操作失败: {e}")
        if os.path.exists(backup):
            shutil.copy2(backup, file_path)
            os.remove(backup)
        return None


def restore_file(file_path, backup):
    """从备份还原文件内容，并更新时间戳以确保触发后续的增量编译。"""
    try:
        shutil.copy2(backup, file_path)
        touch_file(file_path)
        os.remove(backup)
        unregister_backup(file_path)
    except Exception as e:
        print(f"      异常: 文件还原操作失败: {e}")


# ----------------------------------------------
# 核心构建逻辑
# ----------------------------------------------
def resolve_project_path(sln_path, file_path):
    """
    针对 Out-of-Source Build 架构的项目文件寻址逻辑：
    直接进入传入的 .sln 所在目录，定位对应的 .vcxproj，跨越源码目录与构建目录的物理隔离。
    """
    if not sln_path or not sln_path.endswith('.sln'):
        return None

    # 1. 获取解决方案所在的绝对路径目录 (例如: D:\ZW3D\...\out\build\0_MSBuild\cad)
    sln_dir = os.path.dirname(sln_path)
    
    # 2. 提取解决方案名称前缀（例如 "cad.sln" -> "cad"）
    sln_basename = os.path.basename(sln_path)
    sln_name = os.path.splitext(sln_basename)[0]

    # 核心策略 1：同名优先匹配
    # 在标准的模块化工程中，.sln 与其主构建项目 .vcxproj 通常同名
    target_vcxproj = os.path.join(sln_dir, f"{sln_name}.vcxproj")
    if os.path.exists(target_vcxproj):
        return target_vcxproj

    # 核心策略 2：基于同级目录扫描的后备匹配机制
    if os.path.isdir(sln_dir):
        candidates = [f for f in os.listdir(sln_dir) if f.endswith(".vcxproj")]
        
        # 场景 A：当前构建目录下仅存在唯一 .vcxproj 文件
        if len(candidates) == 1:
            return os.path.join(sln_dir, candidates[0])
            
        # 场景 B：存在多个 .vcxproj 文件，提取当前源码文件所属的一级目录（模块名）进行模糊匹配
        if file_path:
            norm_file_path = os.path.normpath(file_path)
            parts = norm_file_path.split(os.sep)
            if parts:
                module_name = parts[0].lower()
                for vcxproj in candidates:
                    if module_name in vcxproj.lower():
                        return os.path.join(sln_dir, vcxproj)

    # 兜底策略：未找到匹配的子项目文件，退回使用全局 .sln
    return None


# ----------------------------------------------
# 主流程：批量合并测试 + 降级逐行排查 (双端验证 + 逻辑优化)
# ----------------------------------------------
def process_with_merged_restore(
    file_groups, sln_path, msbuild_path, args, prefix, writer, fout
):
    pass_count = 0
    total_processed = 0
    file_list = list(file_groups.items())

    for idx, (file_path, rows) in enumerate(file_list):
        full_path = os.path.join(prefix, file_path)
        line_numbers = [int(r[2]) for r in rows]
        total_processed += len(rows)

        print(f"\n{'-'*60}")
        print(f"进度: [{idx+1}/{len(file_list)}] 文件: {file_path}")
        print(f"   待测行号: {line_numbers}")

        if idx > 0 and idx % 20 == 0:
            print(f"\n   状态: 已处理 {idx} 个文件，执行预防性进程清理...")
            kill_vs_processes()
            time.sleep(3)

        maybe_cooldown_environment(args.max_failures)

        if not os.path.exists(full_path):
            print("   警告: 目标文件不存在，跳过当前项")
            continue

        # 步骤1：对当前文件提取的待测行执行批量注释
        result = comment_lines(full_path, line_numbers)
        if result is None:
            print("   错误: 代码注释执行失败，跳过当前项")
            continue

        backup, originals = result
        valid_rows = [r for r in rows if originals.get(int(r[2])) is not None]

        if not valid_rows:
            print("   提示: 目标行均已处于注释状态，跳过验证")
            restore_file(full_path, backup)
            continue

        # 步骤2：定位并应用 .vcxproj 优化
        vcxproj = resolve_project_path(sln_path, full_path)
        if vcxproj and args.use_project_opt:
            build_target = vcxproj
            print(f"   配置: 启用子项目优化 -> {os.path.basename(vcxproj)}")
        else:
            build_target = sln_path

        # 步骤3：执行批量双端构建验证
        print(f"   状态: 启动批量构建验证 (共 {len(valid_rows)} 行)...")
        
        batch_debug_ok, batch_debug_timeout = run_msbuild(
            msbuild_path, build_target, "Build", "Debug", args.timeout_build,
            args.max_cpu_count,
            is_project=bool(vcxproj and args.use_project_opt), enable_binlog=args.enable_binlog
        )
        
        batch_release_ok, batch_release_timeout = False, False
        if batch_debug_ok:
            batch_release_ok, batch_release_timeout = run_msbuild(
                msbuild_path, build_target, "Build", "Release", args.timeout_build,
                args.max_cpu_count,
                is_project=bool(vcxproj and args.use_project_opt), enable_binlog=args.enable_binlog
            )

        passed_rows = []
        if batch_debug_ok and batch_release_ok:
            # 批量测试在 Debug 与 Release 环境下均通过
            passed_rows = valid_rows
            restore_file(full_path, backup)
            print(f"   通过: 批量双端验证完成。{len(passed_rows)} 行代码已确认冗余。记录已保存，代码已自动还原。")
            
        else:
            # 批量测试未通过，恢复源文件状态并执行条件判断
            failed_stage = "Release" if batch_debug_ok else "Debug"
            restore_file(full_path, backup)
            
            # 优化 1：超时熔断。若失败原因为超时，则放弃单行拆分排查以规避无效耗时
            if batch_debug_timeout or batch_release_timeout:
                print(f"   失败: 批量验证在 {failed_stage} 阶段触发超时拦截。")
                print("   策略: 跳过单行排查模式，保留文件原始状态。")
            
            # 优化 2：单行短路评估。若当前文件仅包含 1 行待测代码，直接确认为核心依赖
            elif len(valid_rows) == 1:
                single_line = int(valid_rows[0][2])
                print(f"   失败: 唯一验证目标在 {failed_stage} 构建阶段未能通过。")
                print(f"      结论: 判定第 {single_line} 行为必要依赖，已回滚保护。")
                
            else:
                # 仅在非超时错误且待测行数 > 1 的情况下，转入单行遍历测试
                print(f"   状态: 批量验证在 {failed_stage} 阶段捕获到构建错误。转入逐行排查模式...")
                
                for single_row in valid_rows:
                    single_line = int(single_row[2])
                    print(f"\n      执行: 独立排查第 {single_line} 行...")
                    maybe_cooldown_environment(args.max_failures)
                    
                    single_result = comment_lines(full_path, [single_line])
                    if not single_result: continue
                    single_backup, _ = single_result
                    
                    single_debug_ok, _ = run_msbuild(
                        msbuild_path, build_target, "Build", "Debug", args.timeout_build,
                        args.max_cpu_count,
                        is_project=bool(vcxproj and args.use_project_opt), enable_binlog=args.enable_binlog
                    )
                    
                    single_release_ok = False
                    if single_debug_ok:
                        single_release_ok, _ = run_msbuild(
                            msbuild_path, build_target, "Build", "Release", args.timeout_build,
                            args.max_cpu_count,
                            is_project=bool(vcxproj and args.use_project_opt), enable_binlog=args.enable_binlog
                        )
                    
                    if single_debug_ok and single_release_ok:
                        passed_rows.append(single_row)
                        restore_file(full_path, single_backup)
                        print(f"      通过: 第 {single_line} 行为非必要代码，已记录并还原。")
                    else:
                        restore_file(full_path, single_backup)
                        print(f"      失败: 第 {single_line} 行为核心依赖，已回滚。")

        # 步骤4：将验证通过的记录持续写入结果文件
        for row in passed_rows:
            writer.writerow(row)
            fout.flush()
            pass_count += 1

    return pass_count, total_processed


# ----------------------------------------------
# 命令行参数解析与入口
# ----------------------------------------------
def positive_int(value):
    try:
        parsed = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("必须为正整数") from e
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须为正整数")
    return parsed


def main():
    global_start = time.time()

    parser = argparse.ArgumentParser(description="Fast Build 增量测试工具 v2")
    parser.add_argument("--csv", default="output.csv", help="待验证的 CSV 文件路径")
    parser.add_argument("-s", "--sln", required=True, help="解决方案 (.sln) 的绝对路径")
    parser.add_argument(
        "--msbuild",
        default=r"C:\Program Files\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe",
    )
    parser.add_argument("--timeout-build", type=int, default=600, help="单次构建超时时长限制（秒）")
    parser.add_argument(
        "--max-cpu-count",
        type=int,
        default=default_msbuild_cpu_count(),
        help="MSBuild 编译最大并行度，默认使用本机逻辑核数的一半",
    )
    parser.add_argument(
        "--max-failures",
        type=positive_int,
        default=3,
        help="连续构建失败达到该阈值后，执行一次环境冷却保护",
    )
    
    # 关键修复 3：保留并说明支持多模块联合过滤的参数特性
    parser.add_argument("--module", default="", help="过滤指定子模块，支持逗号分隔多个模块 (如: cad,geometry)，留空则验证所有记录")
    parser.add_argument(
        "--use-project-opt",
        action="store_true",
        default=True,
        help="启用 .vcxproj 项目级寻址优化（默认开启）",
    )
    parser.add_argument(
        "--no-project-opt",
        dest="use_project_opt",
        action="store_false",
        help="禁用项目级优化，强制退回使用 .sln 文件构建",
    )
    parser.add_argument(
        "--enable-binlog",
        action="store_true",
        help="开启 MSBuild 二进制结构化日志功能 (仅限调试排错使用，可能引发高额 I/O 开销)"
    )
    args = parser.parse_args()

    out_csv = f"build_passed_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    # 基于解决方案路径提取项目根目录前缀
    out_index = args.sln.rfind(os.sep + "out" + os.sep)
    prefix = args.sln[:out_index + 1] if out_index != -1 else os.path.dirname(args.sln)

    print("=" * 60)
    print(" 工具: Fast Build 增量测试工具 v2")
    print(f" SLN 路径 : {args.sln}")
    print(f" CSV 文件 : {args.csv}")
    print(f" 输出文件 : {out_csv}")
    print(f" 目标模块 : {args.module if args.module else '未限制 (全量运行)'}")
    print(f" 最大并发 : {args.max_cpu_count}")
    print(f" 冷却阈值 : 连续失败 {args.max_failures} 次")
    print(f" 项目优化 : {'已启用' if args.use_project_opt else '未启用'}")
    print(f" 日志调试 : {'已启用 (警告: I/O 密集)' if args.enable_binlog else '未启用'}")
    print("=" * 60)

    # 读取并初始化过滤目标记录
    all_rows = []
    with open(args.csv, "r", encoding="utf-8-sig") as fin:
        reader = csv.reader(fin)
        headers = next(reader)
        for row in reader:
            if len(row) < 4:
                continue
            module = row[0].strip()
            
            if args.module:
                target_modules = [m.strip() for m in args.module.split(',')]
                if module not in target_modules:
                    continue
                    
            all_rows.append(row)

    if not all_rows:
        print("提示: 数据源过滤后未包含有效记录，进程退出。")
        return

    # 按物理文件路径聚合记录项，维持原始依赖分析序列
    file_groups = {}
    for row in all_rows:
        fp = row[1]
        file_groups.setdefault(fp, []).append(row)

    install_cleanup_handlers()
    recover_leftover_backups(file_groups, prefix)
    kill_vs_processes()

    print(f"\n统计: 有效读取 {len(all_rows)} 条记录，整合映射为 {len(file_groups)} 个物理文件。\n")

    # 进入验证主循环
    try:
        if not run_baseline_validation(
            args.sln, args.msbuild, args.timeout_build, args.max_cpu_count, args.enable_binlog
        ):
            return

        with open(out_csv, "w", newline="", encoding="utf-8-sig") as fout:
            writer = csv.writer(fout)
            writer.writerow(headers)

            pass_count, total_processed = process_with_merged_restore(
                file_groups, args.sln, args.msbuild, args, prefix, writer, fout
            )
    finally:
        kill_vs_processes()
        cleanup_active_backups()

    # 计算与格式化总执行耗时
    elapsed = time.time() - global_start
    h, rem = divmod(elapsed, 3600)
    m, s = divmod(rem, 60)

    print("\n" + "=" * 60)
    print("状态: 增量构建测试任务执行完毕。")
    print(f"总计检测条目 : {total_processed} 行")
    print(f"确认冗余移除 : {pass_count} 行")
    print(f"数据保存路径 : {out_csv}")
    print(f"流程执行总耗时: {int(h)} 小时 {int(m)} 分钟 {s:.1f} 秒")
    print("=" * 60)


if __name__ == "__main__":
    main()
