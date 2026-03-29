## compare.py
import pandas as pd
import argparse
import os

def normalize_path(file_path):
    if pd.isna(file_path):
        return ""
    # 统一斜杠，并全部转换为小写，消除 Windows 下的盘符和目录大小写差异
    return os.path.normpath(str(file_path)).replace("\\", "/").strip().lower()

def clean_code_line(code):
    if pd.isna(code):
        return ""
    # 消除工具输出差异：比如 "#include <string>" 和 "#include<string>" 视为相同
    return str(code).replace(" ", "").strip()

def main():
    parser = argparse.ArgumentParser(description="Compare two CSV files and save matching, non-matching, and union rows.")
    parser.add_argument('file_a', type=str, help="Path to the first CSV file (file_a.csv)")
    parser.add_argument('file_b', type=str, help="Path to the second CSV file (file_b.csv)")
    args = parser.parse_args()

    # 读取CSV文件
    df_a = pd.read_csv(args.file_a, dtype=str)
    df_b = pd.read_csv(args.file_b, dtype=str)

    # 修剪列名
    df_a.columns = df_a.columns.str.strip()
    df_b.columns = df_b.columns.str.strip()

    # 兼容处理：检查到底是 'Line Number' 还是 'Line_number'
    line_col_a = 'Line_number' if 'Line_number' in df_a.columns else 'Line Number'
    line_col_b = 'Line_number' if 'Line_number' in df_b.columns else 'Line Number'
    
    # 统一重命名为标准的 'Line Number'
    df_a.rename(columns={line_col_a: 'Line Number'}, inplace=True)
    df_b.rename(columns={line_col_b: 'Line Number'}, inplace=True)

    required_columns = ['Submodule', 'File Path', 'Line Number', 'Code Line']
    for col in required_columns:
        if col not in df_a.columns:
            print(f"❌ 错误: 文件 A 缺少列: {col}")
            exit(1)
        if col not in df_b.columns:
            print(f"❌ 错误: 文件 B 缺少列: {col}")
            exit(1)

    # 数据清洗：标准化路径、代码行、去除 NaN
    for df in [df_a, df_b]:
        df['File Path'] = df['File Path'].apply(normalize_path)
        df['Code Line'] = df['Code Line'].apply(clean_code_line)
        df['Line Number'] = df['Line Number'].fillna("0").str.strip()
        df['Submodule'] = df['Submodule'].fillna("Unknown").str.strip()

    # 内部去重
    df_a_clean = df_a.drop_duplicates(subset=required_columns)
    df_b_clean = df_b.drop_duplicates(subset=required_columns)

    # ================= 核心逻辑：交集、差集、并集 =================
    # 1. 交集 (Same in both)
    same_rows = pd.merge(df_b_clean, df_a_clean, on=required_columns, how='inner')

    # 2. 差集 (Only in B / Only in A)
    diff_b = pd.merge(df_b_clean, df_a_clean, on=required_columns, how='left', indicator=True)
    diff_b = diff_b[diff_b['_merge'] == 'left_only'].drop(columns=['_merge'])

    diff_a = pd.merge(df_a_clean, df_b_clean, on=required_columns, how='left', indicator=True)
    diff_a = diff_a[diff_a['_merge'] == 'left_only'].drop(columns=['_merge'])

    # 3. 并集 (Union of A and B) -> 新增功能！
    # 将两个表上下拼接，然后去重，就得到了完美的并集
    union_rows = pd.concat([df_a_clean, df_b_clean], ignore_index=True)
    union_rows = union_rows.drop_duplicates(subset=required_columns)
    # ==============================================================

    # 动态生成带前缀的输出文件名
    base_name_a = os.path.splitext(os.path.basename(args.file_a))[0]
    base_name_b = os.path.splitext(os.path.basename(args.file_b))[0]
    
    same_filename = f"compare_same_{base_name_a}_vs_{base_name_b}.csv"
    diff_b_filename = f"compare_only_in_{base_name_b}.csv"
    diff_a_filename = f"compare_only_in_{base_name_a}.csv"
    union_filename = f"compare_UNION_{base_name_a}_and_{base_name_b}.csv" # 并集文件名

    # 保存结果 
    same_rows.to_csv(same_filename, index=False, encoding='utf-8-sig')
    diff_b.to_csv(diff_b_filename, index=False, encoding='utf-8-sig')
    diff_a.to_csv(diff_a_filename, index=False, encoding='utf-8-sig')
    union_rows.to_csv(union_filename, index=False, encoding='utf-8-sig') # 保存并集

    # 打印统计信息
    print(f"\n=== 比较结果统计 ===")
    print(f"文件 A 去重后行数: {len(df_a_clean)}")
    print(f"文件 B 去重后行数: {len(df_b_clean)}")
    print(f"----------------------")
    print(f"【交集】两者共同发现的行数: {len(same_rows)}")
    print(f"【差集】仅 A 发现的行数: {len(diff_a)}")
    print(f"【差集】仅 B 发现的行数: {len(diff_b)}")
    print(f"【并集】合并后的总嫌疑清单: {len(union_rows)}")

    print(f"\n=== 文件已生成 ===")
    print(f"📦 并集文件 (推荐用于验证): {union_filename}")
    print(f"📁 交集文件: {same_filename}")
    print(f"📁 差集文件 (仅A): {diff_a_filename}")
    print(f"📁 差集文件 (仅B): {diff_b_filename}")

if __name__ == "__main__":
    main()