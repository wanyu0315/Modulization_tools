import xml.etree.ElementTree as ET
import csv
import argparse
import os

def process_xml_report_to_csv(xml_file, output_csv_path, prefix_path):
    error_data = []
    prefix_path = os.path.normpath(prefix_path)
    print(f"正在解析 JetBrains XML 报告: {xml_file}")
    
    try:
        # 使用 XML 解析器加载文件
        tree = ET.parse(xml_file)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"❌ 读取 XML 文件失败，请检查文件格式是否完好: {e}")
        return
    except Exception as e:
        print(f"❌ 发生未知错误: {e}")
        return

    count = 0

    # JetBrains 的报告通常把具体问题放在 <Issues> -> <Project> -> <Issue> 结构下
    # 这里我们使用 XPath '//Issue' 直接查找整篇文档中所有的 Issue 节点
    for issue in root.findall('.//Issue'):
        type_id = issue.get('TypeId')
        
        # 匹配冗余头文件的错误类型 (通常是 CppUnusedIncludeDirective)
        if type_id == 'CppUnusedIncludeDirective':
            # 提取文件路径和行号
            file_rel_path = issue.get('File')
            line_str = issue.get('Line')
            
            if not file_rel_path or not line_str:
                continue
                
            line_number = int(line_str)
            
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
        print("未在 XML 报告中提取到任何满足条件的冗余头文件数据。")

def main():
    parser = argparse.ArgumentParser(description="Parse JetBrains InspectCode XML report directly to CSV.")
    parser.add_argument('-i', '--input', required=True, help="输入的报告文件路径 (XML 格式)")
    parser.add_argument('-o', '--output', default='output.csv', help="输出的 CSV 文件路径")
    parser.add_argument('-p', '--prefix', required=True, help="本地源码的根目录绝对路径")
    
    args = parser.parse_args()
    
    process_xml_report_to_csv(args.input, args.output, args.prefix)

if __name__ == "__main__":
    main()
