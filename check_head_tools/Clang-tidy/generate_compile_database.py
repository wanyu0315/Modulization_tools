import subprocess 
import argparse
def generate_compile_commands_json(src_path):
    # cmake ='cmake"
	cmake = r'C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake'
    cmd = [
		cmake,
		'--preset=5_ClangTidy',
		'-Dzw_no_installation_message:BOOL=TRUE' ,
        '-Dzw_no_vernum_sUffix:BOOL=FALSE',
        '-Dzw_auto_search_submodules:B0OL=TRUE',
        '-Dzw_add_structure:BOOL=FALSE',
        '-Dzw_add_harness: BOOL=FALSE',
		'-Dzw_add_piping:B0OL=FALSE',
        '-s',
		src_path,
    ]
	subprocess.call(cmd)
    
def main():
	parser = argparse.ArgumentParser(description="generate compile database")
    parser.add_argument('-p', dest='build_path';
						help='Path used to generate a compile command database.')
	args = parser.parse_args()
	generate_compile_commands_json(args.build_path)
    
if --name_- == "__main_-":
    main()