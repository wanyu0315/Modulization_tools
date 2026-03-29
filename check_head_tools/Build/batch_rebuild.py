import csv
import os
import shutil
import subprocess
import argparse
import time
import tempfile

# ================= 基础进程与系统操作 =================
_last_kill_time = 0.0


def default_msbuild_cpu_count():
    """
    默认预留 4 个逻辑核给系统和其他进程，并限制最大并行度为 16。
    """
    cpu_total = os.cpu_count() or 1
    return max(1, min(cpu_total - 4, 16))

def kill_vs_processes():
    """
    强制终止底层编译与链接进程，释放物理文件锁。
    修复：每个 /IM 必须单独作为参数传递，原代码缺少 '/' 前缀导致命令无效。
    冷却：3 秒内重复调用直接跳过，避免二分搜索中累积大量 sleep。
    """
    global _last_kill_time
    now = time.time()
    if now - _last_kill_time < 3.0:
        return
    targets = [
        "VBCSCompiler.exe", "mspdbsrv.exe",
        "MSBuild.exe", "cl.exe", "link.exe"
    ]
    for name in targets:
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", name],  # 每个进程单独一条命令
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    time.sleep(2)  # 等待文件锁完全释放
    _last_kill_time = time.time()


def tail_text_file(path, max_chars=3000):
    """仅读取日志尾部，避免把大型构建输出全部灌进内存。"""
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


def touch_file(path):
    try:
        os.utime(path, None)
    except Exception:
        pass


# ================= 核心构建模块 =================
def run_msbuild(msbuild_path, sln_path, target, config, timeout, max_cpu_count, cl_mp_count):
    """执行单次 MSBuild，不在内部调用 kill（由上层统一管理）。"""
    if not os.path.exists(msbuild_path) or not os.path.exists(sln_path):
        return False

    cmd = [
        msbuild_path,
        sln_path,
        f"/t:{target}",
        f"/p:Configuration={config}",
        "/p:Platform=x64",
        f"/m:{max_cpu_count}",
        "/p:BuildInParallel=true",
        "/p:MultiProcessorCompilation=true",
        "/nr:false",
        "/nologo",
        "/clp:ErrorsOnly;NoSummary",
    ]
    if cl_mp_count > 0:
        cmd.append(f"/p:CL_MPCount={cl_mp_count}")

    print(f"      启动: MSBuild /t:{target} /p:Configuration={config}")
    start_time = time.time()
    log_path = None
    process = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{config.lower()}.log") as log_file:
            log_path = log_file.name
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"      超时 ({timeout}s)，强制终止")
        process.kill()
        process.wait()
        kill_vs_processes()
        return False
    except KeyboardInterrupt:
        print("\n      收到中断信号，清理进程...")
        process.kill()
        process.wait()
        kill_vs_processes()
        raise
    except Exception:
        if "process" in locals():
            try:
                process.kill()
                process.wait()
            except Exception:
                pass
        kill_vs_processes()
        raise
    finally:
        if log_path and os.path.exists(log_path) and process and process.returncode == 0:
            try:
                os.remove(log_path)
            except OSError:
                pass

    elapsed = time.time() - start_time
    ok = process.returncode == 0
    print(f"      结果: {'成功' if ok else '❌ 失败'} (耗时: {elapsed:.1f}s)")
    if not ok and log_path:
        tail = tail_text_file(log_path)
        if tail:
            print(tail)
    return ok


def run_rebuild_dual_config(msbuild_path, sln_path, timeout, max_cpu_count, cl_mp_count):
    """
    双端 Rebuild 验证。
    默认依赖 /nr:false 关闭节点复用，仅在异常场景清理残留进程。
    """
    debug_ok = run_msbuild(
        msbuild_path, sln_path, "Rebuild", "Debug", timeout, max_cpu_count, cl_mp_count
    )
    if not debug_ok:
        kill_vs_processes()
        return False

    release_ok = run_msbuild(
        msbuild_path, sln_path, "Rebuild", "Release", timeout, max_cpu_count, cl_mp_count
    )
    if not release_ok:
        kill_vs_processes()
    return release_ok


# ================= 物理文件状态管理 =================
def apply_comments_for_rows(rows, prefix, backup_registry):
    """
    对指定行列表注释代码，按文件聚合减少 I/O。
    返回备份记录列表 [(file_path, backup_path), ...]
    """
    file_groups = {}
    for r in rows:
        # 修复：明确使用字段名，避免依赖列顺序
        file_path_val = r.get("File Path", "").strip()
        if not file_path_val:
            continue
        file_groups.setdefault(file_path_val, []).append(r)

    backups = []
    for file_rel_path, group in file_groups.items():
        file_path = os.path.normpath(os.path.join(prefix, file_rel_path))
        backup_path = file_path + ".bak"
        if not os.path.exists(file_path):
            continue

        with open(file_path, "r", encoding="utf-8", errors="ignore") as src:
            lines = src.readlines()

        modified = False
        for r in group:
            line_str = r.get("Line Number", "").strip()
            if not line_str:
                continue
            try:
                line_no = int(line_str)
            except ValueError:
                continue
            if 0 < line_no <= len(lines):
                orig = lines[line_no - 1]
                stripped = orig.strip()
                if not stripped.startswith("//") and not stripped.startswith("/*"):
                    lines[line_no - 1] = f"// {orig}"
                    modified = True

        if modified:
            # 强制覆盖备份，避免使用过期的 .bak 文件
            shutil.copy2(file_path, backup_path)
            with open(file_path, "w", encoding="utf-8", errors="ignore") as dst:
                dst.writelines(lines)
            touch_file(file_path)
            backups.append((file_path, backup_path))
            backup_registry.add(backup_path)

    return backups


def restore_backups(backups):
    """根据备份记录全量回滚物理文件状态。"""
    for file_path, backup_path in backups:
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, file_path)
            touch_file(file_path)
            os.remove(backup_path)


# ================= 二分逼近核心算法 =================
def bisection_search(
    candidates, known_safe, sln_path, msbuild_path, timeout, prefix, result_map,
    flush_csv_callback, max_cpu_count, cl_mp_count, backup_registry, depth=1
):
    """
    递归式 Delta Debugging 算法（带有实时写入 CSV 的回调能力）。
    """
    indent = "  " * depth

    if not candidates:
        return []

    print(f"\n{indent}[深度 {depth}] 测试区间: {len(candidates)} 项, "
          f"已确认安全基线: {len(known_safe)} 项")

    # ── 只注释本次新增的 candidates，known_safe 由上层保持注释状态
    backups = apply_comments_for_rows(candidates, prefix, backup_registry)
    try:
        is_success = run_rebuild_dual_config(
            msbuild_path, sln_path, timeout, max_cpu_count, cl_mp_count
        )
    except Exception:
        restore_backups(backups)
        raise

    if is_success:
        # 整个区间都是安全的，保持注释状态（不还原）
        print(f"{indent} 通过: {len(candidates)} 项均为安全冗余，保留注释状态 (已实时写入CSV)")

        # 不删除备份文件，保留到脚本结束时统一清理，防止崩溃后无法恢复
        # 备份文件会在 main() 最后统一删除

        #  核心修改：实时更新状态表并落盘
        for c in candidates:
            result_map[c["_id"]] = "Y"
        flush_csv_callback()

        return candidates

    # 失败：还原本次 candidates 的注释
    restore_backups(backups)

    if len(candidates) == 1:
        # 单项失败 = 核心依赖，不可删除
        file_path_val = candidates[0].get('File Path', '未知文件')
        line_val = candidates[0].get('Line Number', '未知行')
        print(f"{indent} 核心依赖: {file_path_val} (行: {line_val}) (已实时写入CSV)")
        
        #  核心修改：实时更新状态表并落盘
        result_map[candidates[0]["_id"]] = "N"
        flush_csv_callback()
        
        return []

    # 区间内有冲突，二分拆解
    print(f"{indent}  区间内含依赖冲突，执行二分拆解")
    mid = len(candidates) // 2
    left_half = candidates[:mid]
    right_half = candidates[mid:]

    # 左侧探索（known_safe 基线不变）
    safe_left = bisection_search(
        left_half, known_safe, sln_path, msbuild_path, timeout, prefix, result_map,
        flush_csv_callback, max_cpu_count, cl_mp_count, backup_registry, depth + 1
    )

    # 右侧探索前，safe_left 已经在磁盘上保持注释状态（因为成功时不还原）
    # 所以直接把 safe_left 加入基线即可，无需重新注释
    safe_right = bisection_search(
        right_half, known_safe + safe_left,
        sln_path, msbuild_path, timeout, prefix, result_map, flush_csv_callback,
        max_cpu_count, cl_mp_count, backup_registry, depth + 1
    )

    return safe_left + safe_right


# ================= 主流程 =================
def main():
    global_start_time = time.time()

    parser = argparse.ArgumentParser(description="阶段二：二分逼近式全局 Rebuild 验证")
    parser.add_argument("--csv", required=True, help="输入 CSV 文件路径")
    parser.add_argument("-s", "--sln", required=True, help="顶层 .sln 绝对路径")
    parser.add_argument(
        "--msbuild",
        default=r"C:\Program Files\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe",
    )
    parser.add_argument("--timeout", type=int, default=7200, help="单次 Rebuild 超时（秒）")
    parser.add_argument("--module", default="", help="限定子模块，逗号分隔，空则全域")
    parser.add_argument("--max-cpu-count", type=int, default=default_msbuild_cpu_count(), help="传给 MSBuild 的 /m 并行度")
    parser.add_argument("--cl-mp-count", type=int, default=0, help="传给 cl.exe 的 CL_MPCount；0 表示不显式设置")
    args = parser.parse_args()

    # 修复：使用 rfind 从右向左查找最后一个 "out"，避免路径中多个 out 导致截取错误
    out_index = args.sln.rfind(os.sep + "out" + os.sep)
    if out_index != -1:
        prefix = args.sln[:out_index + 1]  # 保留分隔符
    else:
        prefix = os.path.dirname(args.sln)
    
    # 根据传入的 module 参数动态生成输出文件名
    if args.module:
        safe_module_name = args.module.replace(',', '_')
        out_csv = f"rebuild_result_{safe_module_name}.csv"
    else:
        out_csv = "rebuild_result_global.csv"

    print("=" * 70)
    print(f"  二分逼近 Rebuild 验证系统 (具备防崩溃实时存档)")
    print(f"  数据源  : {args.csv}")
    print(f"  SLN     : {args.sln}")
    print(f"  作用域  : {args.module if args.module else '全域'}")
    print(f"  输出    : {out_csv}")
    print(f"  并行度  : /m:{args.max_cpu_count}, CL_MPCount={args.cl_mp_count or 'auto'}")
    print("=" * 70)

    all_rows = []
    target_rows = []
    target_modules = (
        [m.strip() for m in args.module.split(",")] if args.module else []
    )

    with open(args.csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        
        # 增加容错：无视表头名称强制抓取第一列作为模块名
        module_col_name = fieldnames[0] if fieldnames else ""
        
        for idx, row in enumerate(reader):
            row["_id"] = str(idx)
            all_rows.append(row)
            module_name = str(row.get(module_col_name, "")).strip()
            if not target_modules or module_name in target_modules:
                target_rows.append(row)

    if not target_rows:
        print("未提取到有效测试集合，退出。")
        return

    print(f"\n[1/3] 数据解析完成，{len(target_rows)} 条记录进入二分队列")

    #  建立状态表并定义实时写入闭包
    new_fieldnames = fieldnames + ["Rebuild_Passed"]
    result_map = {r["_id"]: "-" for r in all_rows}
    backup_registry = set()
    for r in target_rows:
        result_map[r["_id"]] = "Pending"  # 初始化状态为等待测试

    def flush_csv():
        """将当前内存中的所有排查状态强行写入 CSV 硬盘文件中"""
        with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=new_fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in all_rows:
                r_out = dict(row) # 拷贝字典以避免修改原始数据
                r_out["Rebuild_Passed"] = result_map.get(r_out["_id"], "-")
                writer.writerow(r_out)
                
    # 第一次初始化写入，产生占位符文件
    flush_csv()

    safe_rows = []
    try:
        kill_vs_processes()

        # ── 执行前先做一次全量 Rebuild，确认基线干净
        print("\n[基线验证] 执行初始 Rebuild，确认当前源码可正常编译...")
        baseline_ok = run_rebuild_dual_config(
            args.msbuild, args.sln, args.timeout, args.max_cpu_count, args.cl_mp_count
        )
        if not baseline_ok:
            print(" 基线 Rebuild 失败！请先修复编译错误再运行本脚本。")
            return
        print(" 基线验证通过，开始二分搜索\n")

        print("[2/3] 启动二分逼近搜索（此阶段所有结论将实时写入 CSV）...")
        safe_rows = bisection_search(
            target_rows, [], args.sln, args.msbuild, args.timeout, prefix, result_map,
            flush_csv, args.max_cpu_count, args.cl_mp_count, backup_registry
        )

        print("\n[3/3] 排查全剧终。")
    finally:
        kill_vs_processes()

        # 清理所有 .bak 备份文件
        print("\n[清理] 删除所有备份文件...")
        bak_count = 0
        for bak_path in sorted(backup_registry):
            if os.path.exists(bak_path):
                try:
                    os.remove(bak_path)
                    bak_count += 1
                except Exception:
                    pass
        print(f"[清理] 已删除 {bak_count} 个备份文件")

    elapsed = time.time() - global_start_time
    h, rem = divmod(elapsed, 3600)
    m, s = divmod(rem, 60)

    print("\n" + "=" * 70)
    print(f"  执行完毕")
    print(f"  目标集合总量  : {len(target_rows)} 项")
    print(f"  确认安全冗余  : {len(safe_rows)} 项 (Y)")
    print(f"  拦截核心依赖  : {len(target_rows) - len(safe_rows)} 项 (N)")
    print(f"  输出报告      : {out_csv} (已完成最终同步)")
    print(f"  总耗时        : {int(h)}h {int(m)}m {s:.1f}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
