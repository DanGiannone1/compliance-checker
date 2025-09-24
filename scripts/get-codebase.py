import argparse
from pathlib import Path
import sys
import os
import fnmatch

base_project_dir = Path('D:/projects/compliance-checker')
default_output_path = base_project_dir / 'scripts' / 'codebase.txt'

# Hardcoded files to include (add your specific files here)
HARDCODED_FILES = [
    # Example files - modify this list as needed
    'backend/app.py',
    'backend/prompts.py',
    'backend/pdf_to_markdown_with_image_descriptions.py',
    'backend/adls.py',
    'frontend/src/components/DocumentValidator.tsx',
        # Add more specific files here...
    ]

def parse_arguments():
    parser = argparse.ArgumentParser(description="Capture codebase files...")
    parser.add_argument(
        'folders',
        type=str,
        nargs='*',
        help="Folder paths to capture files from..."
    )
    parser.add_argument(
        '-o', '--output',
        type=str,
        default=str(default_output_path),
        help="Output file path."
    )
    parser.add_argument(
        '--hardcoded-only',
        action='store_true',
        help="Only include hardcoded files, ignore pattern matching"
    )
    return parser.parse_args()

def get_hardcoded_files():
    """Get the hardcoded files that exist"""
    filtered_files = []
    
    for file_path in HARDCODED_FILES:
        full_path = base_project_dir / file_path
        if full_path.is_file():
            filtered_files.append((base_project_dir, full_path.resolve()))
            print(f"Including hardcoded file: {file_path}")
        else:
            print(f"Warning: Hardcoded file not found: {file_path}")
    
    return filtered_files

def main():
    args = parse_arguments()
    output_file = args.output
    folders = args.folders

    # If hardcoded-only flag is set, only use hardcoded files
    if args.hardcoded_only:
        print("Using hardcoded files only...")
        filtered_files = get_hardcoded_files()
        total_files_found = len(filtered_files)
    else:
        # Original logic for pattern-based file discovery
        # Set up search directories based on provided folders or use base directory
        if not folders:
            search_dirs = [base_project_dir]
        else:
            # Convert and validate folder paths
            search_dirs = []
            for folder in folders:
                folder_path = base_project_dir / folder.strip().rstrip('/\\')
                if not folder_path.is_dir():
                    print(f"Error: The specified folder '{folder}' does not exist under '{base_project_dir}'.")
                    sys.exit(1)
                search_dirs.append(folder_path)

        # Display search directories
        print("\nSearching in directories:")
        for dir_path in search_dirs:
            print(f"- {dir_path}")

        # Define file patterns to include
        include_patterns = [
            # Backend Python files
            'backend/*.py',
            'backend/**/*.py',
            
            # Frontend source files
            'frontend/app/*.ts',
            'frontend/app/**/*.ts',
            'frontend/app/*.tsx',
            'frontend/app/**/*.tsx',
            'frontend/app/*.css',
            'frontend/app/**/*.css',
            
            # Configuration files
            'backend/requirements.txt',
            'backend/example.env',
            'frontend/package.json',
            'frontend/tsconfig*.json',
            
            # Documentation
            'README.md',
        ]

        # Define patterns to exclude
        exclude_patterns = [
            # Build and cache directories
            'node_modules',
            '__pycache__',
            'dist',
            'build',
            '.next',
            'static',
            
            # Environment and IDE files
            '.env',
            '*.env',
            'env',
            '.venv',
            'venv*',
            '.vscode',
            '.idea',
            
            # Package files
            'package-lock.json',
            '*.egg-info',
            
            # Git files
            '.git',
            '.gitignore',
            
            # Log files
            '*.log',
            '*.pyc',
            '*.pyo',
            
            # Script files
            'scripts',
            'get-codebase.py',
        ]

        filtered_files = []
        total_files_found = 0

        # Process each search directory
        for base_dir in search_dirs:
            # Walk through the directory tree
            for root_path, dirs, files in os.walk(base_dir):
                current_dir = Path(root_path)

                # Get relative path from current search directory
                try:
                    relative_dir = current_dir.relative_to(base_dir)
                except ValueError:
                    dirs[:] = []
                    continue

                # Remove excluded directories silently
                dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, pattern) for pattern in exclude_patterns)]

                # Process files in current directory
                for file_name in files:
                    file_path = current_dir / file_name

                    # Skip excluded files silently
                    if any(fnmatch.fnmatch(file_name, pattern) for pattern in exclude_patterns):
                        continue

                    # Check inclusion patterns
                    relative_posix_path = file_path.relative_to(base_dir).as_posix()
                    if any(fnmatch.fnmatch(relative_posix_path, pattern) for pattern in include_patterns):
                        filtered_files.append((base_dir, file_path.resolve()))
                        total_files_found += 1

        # Add hardcoded files to the list (if not using hardcoded-only mode)
        hardcoded_files = get_hardcoded_files()
        for hardcoded_file in hardcoded_files:
            if hardcoded_file not in filtered_files:
                filtered_files.append(hardcoded_file)
                total_files_found += 1

    print(f"\nTotal files included: {total_files_found}")

    if not filtered_files:
        print("No files found matching the specified criteria.")
        sys.exit(0)

    # Ensure output directory exists
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Set up output formatting
    header = "Here is my codebase:\n\n"
    footer = "\n<end codebase> \n\n"

    # Write collected files to output
    try:
        with output_path.open('w', encoding='utf-8', errors='ignore') as f:
            f.write(header)

            # Process each collected file
            for idx, (base_dir, filepath) in enumerate(sorted(filtered_files), 1):
                print(f"Processing file {idx}/{len(filtered_files)}: {filepath}")
                relative_path = filepath.relative_to(base_dir).as_posix()
                
                try:
                    with filepath.open('r', encoding='utf-8', errors='ignore') as code_file:
                        f.write(f"<File: {relative_path}>\n")
                        f.write(code_file.read())
                        f.write('\n' + '-'*80 + '\n')
                except Exception as e:
                    print(f"Error reading file '{filepath}': {e}")
                    f.write(f"\nError reading file '{relative_path}': {e}\n")
                    f.write('-'*80 + '\n')

            f.write(footer)

        print(f"Codebase captured successfully in '{output_file}'.")
    except Exception as e:
        print(f"Error writing to output file '{output_file}': {e}")
        sys.exit(1)

if __name__ == "__main__":
   main()