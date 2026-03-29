##parallel_tidy.py
import os
import subprocess
import argparse
import concurrent.futures
import threading
import time

# 创建一个线程锁，防止多个核心同时往 txt 里写数据时发生文字穿插和错乱
file_lock = threading.Lock()

def scan_single_file(file_path, clang_tidy_exe, comp_db_dir, output_txt):
    """供单个线程执行的任务：扫描一个文件并将结果安全地写入txt"""
    cmd = [
        clang_tidy_exe,
        '-checks=-*,misc-include-cleaner', # 强制：只检查冗余头文件
        '-p', comp_db_dir,
        file_path
    ]
    
    try:
        # 启动 clang-tidy 进程并捕获它的所有输出
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
        output = result.stdout
        
        # 如果有输出报错内容，则加锁安全地写入到最终的 txt 文件中
        if output and output.strip():
            with file_lock:
                with open(output_txt, 'a', encoding='utf-8') as f:
                    f.write(output)
                    f.write("\n")
                    
        print(f"[完成] {os.path.basename(file_path)}")
        
    except Exception as e:
        print(f"[错误] 扫描 {file_path} 时失败: {e}")

def main():
    parser = argparse.ArgumentParser(description="Multi-core Clang-Tidy Scanner")
    parser.add_argument('-m', '--module', required=True, help="要扫描的文件夹路径")
    parser.add_argument('-c', '--compdb', required=True, help="compile_commands.json 所在的目录")
    parser.add_argument('-t', '--tool', required=True, help="clang-tidy.exe 的完整路径")
    parser.add_argument('-o', '--output', required=True, help="最终输出的 txt 报告路径")
    args = parser.parse_args()

    # 1. 收集所有需要扫描的 C/C++ 源文件
    target_extensions = ('.c', '.cpp', '.h', '.hpp')
    files_to_scan = []
    print(f"正在遍历目录查找源码文件: {args.module} ...")
    for root, dirs, files in os.walk(args.module):
        for file in files:
            if file.lower().endswith(target_extensions):
                files_to_scan.append(os.path.join(root, file))
                
    total_files = len(files_to_scan)
    print(f"共找到 {total_files} 个文件准备扫描！")
    
    if total_files == 0:
        return

    # 2. 获取电脑的逻辑 CPU 核心数 (比如 16核 或 32核)
    max_workers = os.cpu_count()
    print(f"火力全开！启动 {max_workers} 个并发线程进行扫描...")
    
    start_time = time.time()

    # 3. 使用线程池并发执行 clang-tidy
    # 注意：因为 subprocess 是调起外部进程，Python的 GIL 不会阻塞它，用 ThreadPoolExecutor 是最高效的
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务到线程池
        futures = [
            executor.submit(scan_single_file, filepath, args.tool, args.compdb, args.output) 
            for filepath in files_to_scan
        ]
        # 等待所有任务完成
        concurrent.futures.wait(futures)

    end_time = time.time()
    minutes = (end_time - start_time) / 60
    print(f"\n✅ 扫描结束！共耗时: {minutes:.2f} 分钟。结果已保存至: {args.output}")

if __name__ == "__main__":
    main()
