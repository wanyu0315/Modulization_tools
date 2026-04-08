import json
import csv
import argparse
import os

def process_sarif_report_to_csv(sarif_file, output_csv_path, prefix_path):
    error_data = []
    prefix_path = os.path.normpath(prefix_path)
    print(f"正在解析 JetBrains SARIF 报告: {sarif_file}")
    
    try:
        with open(sarif_file, 'r', encoding='utf-8') as file:
            sarif_data = json.load(file)
    except json.JSONDecodeError as e:
        print(f"❌ 读取 SARIF JSON 文件失败，请检查文件格式是否完好: {e}")
        return
    except Exception as e:
        print(f"❌ 发生未知错误: {e}")
        return

    count = 0

    # JetBrains 的 SARIF 报告通常将问题放在 runs[*].results[*] 下
    for run in sarif_data.get('runs', []):
        for issue in run.get('results', []):
            rule_id = issue.get('ruleId')

            # 匹配冗余头文件的错误类型 (通常是 CppUnusedIncludeDirective)
            if rule_id != 'CppUnusedIncludeDirective':
                continue

            locations = issue.get('locations', [])
            if not locations:
                continue

            physical_location = locations[0].get('physicalLocation', {})
            artifact_location = physical_location.get('artifactLocation', {})
            region = physical_location.get('region', {})

            file_rel_path = artifact_location.get('uri')
            line_number = region.get('startLine')

            if not file_rel_path or not line_number:
                continue

            # 1. 提取子模块名称 (以路径第一级目录作为子模块名)
            cleaned_location = file_rel_path.replace('\\', '/')
            while cleaned_location.startswith("../"):
                cleaned_location = cleaned_location[3:]

            path_parts = [p for p in cleaned_location.split("/") if p]
            submodule_name = path_parts[0] if path_parts else "UnknownSubmodule"

            # 2. 拼接得到本地源码的绝对路径
            full_file_path = os.path.join(prefix_path, cleaned_location)
            full_file_path = os.path.normpath(full_file_path)

            # 3. 打开源码文件，提取对应的代码行
            code_line = ""
            try:
                with open(full_file_path, 'r', encoding='utf-8', errors='ignore') as src_file:
                    lines = src_file.readlines()
                    if 0 < line_number <= len(lines):
                        code_line = lines[line_number - 1].strip()
                    else:
                        code_line = "Line number out of range"
            except FileNotFoundError:
                code_line = "File not found"
            except Exception as e:
                code_line = f"Error: {str(e)}"

            # 4. 存入结果列表
            error_data.append([submodule_name, cleaned_location, line_number, code_line])
            count += 1

    # 将提取的数据写入 CSV
    if error_data:
        try:
            with open(output_csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['Submodule', 'File Path', 'Line Number', 'Code Line'])
                writer.writerows(error_data)
            print(f"✅ 处理完成！共提取匹配记录: {count} 条")
            print(f"📁 最终结果 CSV 已生成至: {output_csv_path}")
        except Exception as e:
            print(f"❌ 写入 CSV 失败: {e}")
    else:
        print("未在 SARIF 报告中提取到任何满足条件的冗余头文件数据。")

def process_xml_report_to_csv(xml_file, output_csv_path, prefix_path):
    # 为兼容旧调用方保留函数名，实际输入已切换为 SARIF(JSON)
    process_sarif_report_to_csv(xml_file, output_csv_path, prefix_path)

def main():
    parser = argparse.ArgumentParser(description="Parse JetBrains InspectCode SARIF report directly to CSV.")
    parser.add_argument('-i', '--input', required=True, help="输入的报告文件路径 (SARIF/JSON 格式)")
    parser.add_argument('-o', '--output', default='output.csv', help="输出的 CSV 文件路径")
    parser.add_argument('-p', '--prefix', required=True, help="本地源码的根目录绝对路径")
    
    args = parser.parse_args()
    
    process_sarif_report_to_csv(args.input, args.output, args.prefix)

if __name__ == "__main__":
    main()
