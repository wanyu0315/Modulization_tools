import csv
import re
import argparse
import os

def process_clang_tidy_output(txt_file_path, output_csv_path, prefix_path):
    error_data = []
    seen_records = set()

    # 统一路径格式，消除正反斜杠差异
    norm_prefix = os.path.normpath(prefix_path)

    print(f"正在读取分析报告: {txt_file_path}")

    try:
        with open(txt_file_path, 'r', encoding='utf-8') as file:
            content = file.readlines()

        for i, line in enumerate(content):
            line = line.strip()

            # 定位冗余头文件错误
            if '[misc-include-cleaner]' in line and 'included header' in line:
                match = re.match(r'^(?P<file_path>.+?):(?P<line_number>\d+):\d+:', line)
                if match:
                    raw_file_path = match.group('file_path').strip()
                    line_number = match.group('line_number').strip()

                    # 统一路径格式
                    norm_file_path = os.path.normpath(raw_file_path)

                    # 安全地截取子模块名和相对路径
                    if norm_file_path.startswith(norm_prefix):
                        # 剔除前缀，提取相对路径
                        rel_path = norm_file_path[len(norm_prefix):].lstrip(os.sep)
                    else:
                        rel_path = norm_file_path

                    # 提取第一级目录作为子模块名
                    submodule_name = rel_path.split(os.sep)[0] if rel_path else "Unknown"
                    file_path_cleaned = rel_path

                    # 获取具体的代码行内容
                    code_content = ""
                    if i + 1 < len(content):
                        code_line = content[i + 1].strip()
                        if "#include" in code_line:
                            # 兼容有没有 "|" 整线的情况
                            if "|" in code_line:
                                code_content = code_line.split("|", 1)[1].strip()
                            else:
                                code_content = code_line

                    if code_content:
                        record_key = (submodule_name, file_path_cleaned, code_content)
                        if record_key not in seen_records:
                            seen_records.add(record_key)
                            error_data.append([submodule_name, file_path_cleaned, line_number, code_content])

    except Exception as e:
        print(f"解析文件时发生错误: {e}")

    # 写入 CSV 文件
    if error_data:
        try:
            with open(output_csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Submodule', 'File Path', 'Line Number', 'Code Line'])
                writer.writerows(error_data)
            print(f"✅ 成功提取 {len(error_data)} 条冗余头文件记录！CSV 文件已保存至: {output_csv_path}")
        except Exception as e:
            print(f"写入 CSV 失败: {e}")
    else:
        print("未在报告中提取到任何满足条件的数据。")

def main():
    parser = argparse.ArgumentParser(description="Process clang-tidy output and save to CSV.")
    parser.add_argument('-p', dest="txt_file_path", required=True, help="Path to the input clang-tidy output file.")
    parser.add_argument('-s', dest="prefix_path", required=True, help="Project Source prefix to be removed.")
    args = parser.parse_args()

    # 将输出的 CSV 放在与 txt 同级的目录下
    output_dir = os.path.dirname(args.txt_file_path)
    output_csv_path = os.path.join(output_dir, 'tidy_check_output.csv')

    process_clang_tidy_output(args.txt_file_path, output_csv_path, args.prefix_path)

if __name__ == "__main__":
    main()